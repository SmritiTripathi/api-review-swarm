"""Swarm: every persona reviews the same spec in parallel; save all findings."""
import argparse
import asyncio
import json
import time
from pathlib import Path

from llm import DEV_MODEL, FINDINGS_TOOL, call_structured, load_spec_text

USAGE_KEYS = ["input_tokens", "cache_write_tokens", "cache_read_tokens", "output_tokens"]

# Same review rules as the baseline, so the main thing that differs is the
# persona. Only the cap changes: each persona reports fewer, more focused findings.
RULES = """Review the OpenAPI spec provided above, from your perspective.

Rules:
- Report only problems you can point to in the spec text. Do not speculate about server implementation.
- Be specific: every finding must name an exact location and cite evidence from the spec.
- Prefer real problems over nitpicks. Do not pad the list. Report at most {max_findings} findings, most important first.
- Keep every field concise: titles under 12 words, and the evidence and suggested fix each one sentence.

Report your findings in the required format."""


def build_system(spec_text, persona_prompt):
    """Two system blocks. The first (the spec) is identical for every persona and
    marked for caching; the persona's own prompt comes after it.
    Order matters: a cache only matches an identical *prefix* of the request."""
    return [
        {"type": "text",
         "text": f"<spec>\n{spec_text}\n</spec>",
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": persona_prompt},
    ]


async def review(persona, spec_text, model, max_findings):
    """One persona's review. call_structured is ordinary (blocking) code, so
    asyncio.to_thread runs it in a background thread; several can then wait
    on the network at the same time."""
    t0 = time.time()
    try:
        result, usage, stop = await asyncio.to_thread(
            call_structured, model,
            build_system(spec_text, persona["system_prompt"]),
            RULES.format(max_findings=max_findings),
            FINDINGS_TOOL)
    except Exception as e:
        # One reviewer failing must not throw away the other reviewers' work.
        # Record the failure; the run is still saved and the failure is visible.
        seconds = round(time.time() - t0, 1)
        print(f"  {persona['id']} {persona['name']:<10} FAILED after {seconds}s: {e}")
        return {"persona": persona["id"], "name": persona["name"], "error": str(e),
                "usage": {k: 0 for k in USAGE_KEYS}, "seconds": seconds, "findings": []}
    seconds = round(time.time() - t0, 1)

    # Second line of defence: keep only well-formed findings, and say how many
    # were dropped, so bad output is counted rather than silently ignored.
    raw = result.get("findings", [])
    findings = [f for f in raw if isinstance(f, dict)] if isinstance(raw, list) else []
    dropped = (len(raw) if isinstance(raw, list) else 1) - len(findings)
    if dropped:
        print(f"  {persona['id']} WARNING: dropped {dropped} malformed finding(s)")
    for i, f in enumerate(findings, 1):
        f["id"] = f"{persona['id']}-{i:02d}"   # e.g. P3-07
        f["persona"] = persona["id"]
    print(f"  {persona['id']} {persona['name']:<10} {len(findings):2d} findings  "
          f"{seconds:5.1f}s  cache write {usage['cache_write_tokens']:,} / "
          f"read {usage['cache_read_tokens']:,}")
    return {"persona": persona["id"], "name": persona["name"], "usage": usage,
            "stop_reason": stop, "seconds": seconds, "dropped": dropped,
            "findings": findings}


async def run_swarm(personas, spec_text, model, max_findings):
    # A cache entry exists only once the first response has started. Run one
    # persona alone to write the cache, then the rest in parallel to read it.
    first = await review(personas[0], spec_text, model, max_findings)
    rest = await asyncio.gather(
        *(review(p, spec_text, model, max_findings) for p in personas[1:]))
    return [first, *rest]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="data/spec_flawed.json")
    ap.add_argument("--personas", default="data/personas.json")
    ap.add_argument("--model", default=DEV_MODEL)
    ap.add_argument("--max-findings", type=int, default=15)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    personas = json.loads(Path(args.personas).read_text())["personas"]
    spec_text = load_spec_text(args.spec)

    print(f"Swarm of {len(personas)} reviewing {args.spec} with {args.model} ...")
    t0 = time.time()
    reviews = asyncio.run(run_swarm(personas, spec_text, args.model, args.max_findings))
    elapsed = round(time.time() - t0, 1)

    findings = [f for r in reviews for f in r["findings"]]
    usage = {k: sum(r["usage"][k] for r in reviews) for k in USAGE_KEYS}
    failed = [r["persona"] for r in reviews if "error" in r]

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out or f"results/swarm_{args.model}_{stamp}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "method": "swarm", "model": args.model, "spec": args.spec,
        "personas_file": args.personas, "max_findings": args.max_findings,
        "timestamp": stamp, "usage": usage, "seconds": elapsed, "failed": failed,
        "reviews": reviews, "findings": findings,
    }, indent=2))

    print(f"\n{len(findings)} findings (before de-duplication) | {elapsed}s total")
    print("tokens: " + " | ".join(f"{k} {usage[k]:,}" for k in USAGE_KEYS))
    if failed:
        print(f"WARNING: {len(failed)} persona(s) failed: {', '.join(failed)}. "
              "Treat this run as incomplete.")
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()