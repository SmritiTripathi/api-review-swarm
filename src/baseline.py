"""Baseline: one strong single-pass LLM review of an OpenAPI spec."""
import argparse
import json
import time
from pathlib import Path

from llm import DEV_MODEL, FINDINGS_TOOL, call_structured, load_spec_text

SYSTEM = (
    "You are a senior API reviewer who has designed and operated large public "
    "REST APIs. You review OpenAPI specs for developer-experience problems: "
    "anything that would confuse, mislead, or endanger a developer or an AI "
    "agent integrating with the API."
)

USER_TEMPLATE = """Review the OpenAPI spec below.

Rules:
- Report only problems you can point to in the spec text. Do not speculate about server implementation.
- Be specific: every finding must name an exact location and cite evidence from the spec.
- Prefer real problems over nitpicks. Do not pad the list. Report at most 30 findings, most important first.
- Keep every field concise: titles under 12 words, and the evidence and suggested fix each one sentence.
- Consider naming consistency, error handling, documentation quality, security, schema design, pagination, and anything else that would trip up an integrator.

<spec>
{spec}
</spec>

Report your findings with the report_findings tool."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="data/spec_flawed.json")
    ap.add_argument("--model", default=DEV_MODEL)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    user = USER_TEMPLATE.replace("{spec}", load_spec_text(args.spec))

    print(f"Reviewing {args.spec} with {args.model} ...")
    t0 = time.time()
    result, usage, stop = call_structured(args.model, SYSTEM, user, FINDINGS_TOOL)
    elapsed = time.time() - t0

    findings = result.get("findings", [])
    for i, f in enumerate(findings, 1):
        f["id"] = f"B{i:02d}"

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out or f"results/baseline_{args.model}_{stamp}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "method": "baseline", "model": args.model, "spec": args.spec,
        "timestamp": stamp, "usage": usage, "stop_reason": stop,
        "seconds": round(elapsed, 1), "findings": findings,
    }, indent=2))

    print(f"\n{len(findings)} findings | {usage['input_tokens']:,} in / "
          f"{usage['output_tokens']:,} out tokens | {elapsed:.0f}s | stop={stop}")
    print(f"Saved to {out}\n")
    for f in findings:
        print(f"{f['id']}  sev{f.get('severity','?')}  "
              f"{f.get('category','?'):<14} {f.get('location','?')[:60]}")
        print(f"     {f.get('title','')}")


if __name__ == "__main__":
    main()
