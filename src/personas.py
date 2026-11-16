"""Generate reviewer personas from structured attributes."""
import argparse
import json
import random
from pathlib import Path

from llm import FINAL_MODEL, call_structured

SEED = 7

# Fixed per role: who they are and what they are trying to do.
ROLES = [
    {"role": "first-day developer integrating the API for the first time",
     "experience": "1-3 years of professional development",
     "goal": "get a working integration running by the end of the week"},
    {"role": "security engineer reviewing the API before approving it for production",
     "experience": "8+ years in application security",
     "goal": "decide whether the API is safe enough to approve"},
    {"role": "partner integrator building a commercial product on top of the API",
     "experience": "5-8 years building integrations",
     "goal": "build something customers will pay for without surprise breakages"},
    {"role": "finance operations admin who reads the docs to configure workflows and rarely writes code",
     "experience": "6+ years in finance operations, little coding experience",
     "goal": "set up approvals and reporting without engineering help"},
    {"role": "engineer building an AI agent that calls the API through tool definitions",
     "experience": "3-6 years of engineering, 1-2 years building LLM agents",
     "goal": "make the agent complete multi-step tasks without human correction"},
    {"role": "senior API architect who has designed several public APIs",
     "experience": "12+ years designing APIs",
     "goal": "judge whether the API is consistent and can evolve for years"},
]

# Sampled without replacement so no two personas share a value.
PET_PEEVES = [
    "vague descriptions that force guessing",
    "inconsistent naming across endpoints",
    "undocumented failure modes",
    "unclear units, types, or valid values",
    "ambiguous authentication or permissions",
    "operations that behave differently than their names suggest",
    "missing guidance on limits, paging, or retries",
]
CONSTRAINTS = [
    "has 30 minutes to evaluate the API before a meeting",
    "must satisfy a strict compliance review",
    "generates a typed client library from the spec",
    "has no support contact, only the spec itself",
    "integrates several other APIs and expects consistency across them",
    "is comparing this API against a competitor's",
    "must explain the API to non-technical stakeholders",
]
READING_STYLES = [
    "skims headings and parameter tables",
    "reads every description line by line",
    "looks for the happy path first, then hunts for failure cases",
    "compares similar endpoints side by side",
    "focuses on request and response schemas more than prose",
    "reads only what an automated tool would see: names, types, descriptions",
]

PERSONAS_TOOL = {
    "name": "report_personas",
    "description": "Return one fully developed reviewer persona per attribute row, in order.",
    "input_schema": {
        "type": "object",
        "properties": {
            "personas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "A realistic first name and last initial"},
                        "summary": {"type": "string", "description": "One sentence: who they are"},
                        "what_they_notice": {"type": "string",
                                             "description": "Two sentences: what this person tends to spot in an API spec"},
                        "blind_spots": {"type": "string",
                                        "description": "One sentence: what this person tends to overlook"},
                        "system_prompt": {
                            "type": "string",
                            "description": "A second-person prompt (150-250 words) that makes an LLM review an API spec "
                                           "as this person: their background, goal, constraint, pet peeve, and how they read."},
                    },
                    "required": ["name", "summary", "what_they_notice", "blind_spots", "system_prompt"],
                },
            }
        },
        "required": ["personas"],
    },
}

SYSTEM = ("You design realistic reviewer personas for testing how different kinds of people "
          "experience an API. You keep personas distinct, believable, and grounded in their attributes.")

USER_TEMPLATE = """Create one persona for each attribute row below, in the same order.

Rules:
- Stay faithful to every attribute. Do not add attributes or change them.
- Make the personas clearly different from one another in voice, priorities, and blind spots.
- Each system_prompt must instruct the reviewer to report only problems they can point to in the spec text, with an exact location.
- Do not mention any specific API, company, or spec. These personas will be reused on different specs.

Attribute rows (JSON):
{rows}

Return the personas with the report_personas tool."""


def build_rows(seed):
    rng = random.Random(seed)
    peeves = rng.sample(PET_PEEVES, len(ROLES))
    constraints = rng.sample(CONSTRAINTS, len(ROLES))
    styles = rng.sample(READING_STYLES, len(ROLES))
    rows = []
    for i, base in enumerate(ROLES):
        rows.append({**base, "pet_peeve": peeves[i], "constraint": constraints[i],
                     "reading_style": styles[i]})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=FINAL_MODEL)
    ap.add_argument("--out", default="data/personas.json")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    rows = build_rows(args.seed)
    user = USER_TEMPLATE.replace("{rows}", json.dumps(rows, indent=2))
    result, usage, stop = call_structured(args.model, SYSTEM, user, PERSONAS_TOOL)
    personas = result["personas"]

    assert len(personas) == len(rows), f"expected {len(rows)} personas, got {len(personas)}"
    names = [p["name"] for p in personas]
    assert len(set(names)) == len(names), "persona names are not unique"

    out = []
    for i, (row, p) in enumerate(zip(rows, personas), 1):
        assert len(p["system_prompt"]) > 300, f"persona {i} prompt is too short"
        out.append({"id": f"P{i}", "attributes": row, **p})

    Path(args.out).write_text(json.dumps(
        {"seed": args.seed, "model": args.model, "personas": out}, indent=2))

    print(f"Saved {len(out)} personas to {args.out} | {usage['input_tokens']:,} in / "
          f"{usage['output_tokens']:,} out tokens\n")
    for p in out:
        a = p["attributes"]
        print(f"{p['id']}  {p['name']}")
        print(f"    role:      {a['role'][:70]}")
        print(f"    pet peeve: {a['pet_peeve']}")
        print(f"    reads by:  {a['reading_style']}")


if __name__ == "__main__":
    main()
