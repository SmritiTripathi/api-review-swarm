"""Shared helpers for calling Claude with structured (tool-forced) output."""
import json
import time

import anthropic
from dotenv import load_dotenv

load_dotenv()

DEV_MODEL = "claude-haiku-5-5"      # cheap: for building and debugging
FINAL_MODEL = "claude-sonnet-5-5"   # for the runs you will report

client = anthropic.Anthropic()

CATEGORIES = ["naming", "errors", "documentation", "security", "schema-design",
              "pagination", "consistency", "idempotency", "other"]

# A "tool" here is just a JSON schema. Forcing the model to call it guarantees
# structured output we can parse reliably, with no regex on free text.
FINDINGS_TOOL = {
    "name": "report_findings",
    "description": "Report all developer-experience issues found in the API spec.",
    "input_schema": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string",
                                  "description": "Short summary, under 12 words"},
                        "location": {"type": "string",
                                     "description": "Exact location, e.g. 'GET /developer/v1/cards/physical' or 'components.schemas.Foo.properties.bar'"},
                        "category": {"type": "string", "enum": CATEGORIES},
                        "severity": {"type": "integer", "enum": [1, 2, 3],
                                     "description": "1 minor, 2 moderate, 3 serious"},
                        "evidence": {"type": "string",
                                     "description": "What in the spec shows this problem"},
                        "suggested_fix": {"type": "string"},
                    },
                    "required": ["title", "location", "category", "severity",
                                 "evidence", "suggested_fix"],
                },
            }
        },
        "required": ["findings"],
    },
}


def load_spec_text(path):
    """Load a spec as compact JSON (no indentation) to save tokens."""
    with open(path) as f:
        return json.dumps(json.load(f), separators=(",", ":"))


def call_structured(model, system, user, tool, max_tokens=16000, retries=4):
    """Call Claude, force it to answer via `tool`, return (result, usage, stop_reason)."""
    for attempt in range(retries):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
            )
            break
        except (anthropic.RateLimitError, anthropic.APIConnectionError,
                anthropic.InternalServerError) as e:
            wait = 2 ** (attempt + 1)
            print(f"  transient error ({type(e).__name__}); retrying in {wait}s")
            time.sleep(wait)
    else:
        raise RuntimeError("API call failed after retries")

    if resp.stop_reason == "max_tokens":
        raise RuntimeError(
            "Output hit max_tokens and was cut off. Raise max_tokens or ask for shorter output.")
    block = next((b for b in resp.content if b.type == "tool_use"), None)
    if block is None:
        raise RuntimeError(f"No structured output returned (stop_reason={resp.stop_reason})")
    usage = {"input_tokens": resp.usage.input_tokens,
             "output_tokens": resp.usage.output_tokens}
    return block.input, usage, resp.stop_reason
