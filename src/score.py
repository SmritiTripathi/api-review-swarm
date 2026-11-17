"""Score a review run against the answer key.

Stage 1, location filter (plain code): for each planted defect, keep only the
findings that mention its path, schema or field name.
Stage 2, LLM judge: given the rubric, the defect and those candidate findings,
decide hit / partial / miss.

Optionally compare the judge's verdicts with hand labels (--labels) to check
that the judge can be trusted.
"""
import argparse
import asyncio
import csv
import json
import re
import time
from pathlib import Path

from llm import DEV_MODEL, call_structured

# Same text as the "Scoring rubric" section in docs/notes.md.
RUBRIC = """- hit: the finding identifies the planted problem at the right location. Category label, wording and extra detail do not matter. The problem may be stated in the title or the evidence, as long as it is stated as a problem; it still counts if the finding's main topic is a different problem.
- partial: right location, but the planted problem is only implied, or a different problem is described there.
- miss: no finding describes the planted problem.
- A defect can be matched by several findings; one finding can match several defects."""

JUDGE_SYSTEM = f"""You grade an API review against one known, deliberately planted defect.
Decide whether any of the candidate findings detects that defect, using this rubric:

{RUBRIC}

Judge only against the planted defect. A finding about a different real problem
at the same location is not a hit for this defect.

The "A detecting finding" line is the standard to judge against. "What was
changed" is background on how the defect was made: a finding does not need to
name the exact item that was removed or changed, as long as it correctly
describes the resulting problem."""

JUDGE_TOOL = {
    "name": "report_verdict",
    "description": "Report whether the planted defect was detected.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["hit", "partial", "miss"]},
            "matched_finding_ids": {
                "type": "array", "items": {"type": "string"},
                "description": "IDs of findings that detect the defect (hit) or come close (partial). Empty for miss."},
            "reason": {"type": "string", "description": "One sentence explaining the verdict."},
        },
        "required": ["verdict", "matched_finding_ids", "reason"],
    },
}

PATH_RE = re.compile(r"/developer/v1/[\w/{}\-]+")
QUOTED_ID_RE = re.compile(r"'([A-Za-z_]\w{3,})'")   # e.g. 'userId', 'sync_status'


def anchors(defect):
    """Strings that a relevant finding is likely to mention."""
    out = []
    for path in PATH_RE.findall(defect["location"]):
        # Match the path exactly, not as the start of a longer path:
        # /cards/physical must not match /cards/physical/{card_id}.
        out.append(re.compile(re.escape(path) + r"(?![\w/{\-])"))
    m = re.search(r"components\.schemas\.(\w+)", defect["location"])
    if m:
        out.append(re.compile(rf"\b{m.group(1)}\b"))
    for name in QUOTED_ID_RE.findall(defect["detection_hint"]):
        out.append(re.compile(rf"\b{re.escape(name)}\b"))
    return out


def finding_text(f):
    return " ".join(str(f.get(k, "")) for k in ("title", "location", "evidence"))


def candidates(defect, findings):
    pats = anchors(defect)
    return [f for f in findings if any(p.search(finding_text(f)) for p in pats)]


def judge_prompt(defect, cands):
    lines = [f"PLANTED DEFECT {defect['id']}",
             f"Location: {defect['location']}",
             f"What was changed: {defect['what_changed']}",
             f"A detecting finding: {defect['detection_hint']}",
             "", "CANDIDATE FINDINGS"]
    for f in cands:
        lines += [f"[{f['id']}] {f['title']}",
                  f"  location: {f['location']}",
                  f"  evidence: {f['evidence']}"]
    lines += ["", "Report your verdict in the required format."]
    return "\n".join(lines)


async def judge(defect, cands, model, sem):
    if not cands:
        # Nothing mentions this location: a miss, with no API call needed.
        return {"defect": defect["id"], "verdict": "miss", "matched_finding_ids": [],
                "reason": "No finding mentions this location.", "candidates": []}
    async with sem:   # limit how many judge calls run at once
        result, usage, _ = await asyncio.to_thread(
            call_structured, model, JUDGE_SYSTEM, judge_prompt(defect, cands), JUDGE_TOOL)
    valid = {f["id"] for f in cands}
    return {"defect": defect["id"], "verdict": result["verdict"],
            # keep only IDs that were actually shown to the judge
            "matched_finding_ids": [i for i in result["matched_finding_ids"] if i in valid],
            "reason": result["reason"], "candidates": sorted(valid), "usage": usage}


def metrics(verdicts, key):
    sev = {d["id"]: d["severity"] for d in key}
    hits = [v["defect"] for v in verdicts if v["verdict"] == "hit"]
    partial = [v["defect"] for v in verdicts if v["verdict"] == "partial"]
    return {
        "defects": len(key),
        "hits": len(hits),
        "partials": len(partial),
        "recall": round(len(hits) / len(key), 3),
        "recall_incl_partial": round((len(hits) + len(partial)) / len(key), 3),
        "severity_weighted_recall": round(sum(sev[d] for d in hits) / sum(sev.values()), 3),
    }


def compare_with_labels(verdicts, labels_path):
    labels = {r["defect_id"]: r["verdict"].strip().lower()
              for r in csv.DictReader(open(labels_path))}
    rows = [(v["defect"], labels.get(v["defect"], "?"), v["verdict"]) for v in verdicts]
    agree = sum(1 for _, h, j in rows if h == j)
    print(f"\nJudge vs hand labels: {agree}/{len(rows)} agree")
    for d, h, j in rows:
        if h != j:
            print(f"  {d}: hand={h:<7} judge={j}")
    return {"agree": agree, "total": len(rows),
            "disagreements": [{"defect": d, "hand": h, "judge": j}
                              for d, h, j in rows if h != j]}


async def score_all(key, findings, model, concurrency):
    sem = asyncio.Semaphore(concurrency)
    return await asyncio.gather(
        *(judge(d, candidates(d, findings), model, sem) for d in key))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", help="results/<run>.json to score")
    ap.add_argument("--key", default="data/answer_key.json")
    ap.add_argument("--model", default=DEV_MODEL, help="judge model")
    ap.add_argument("--labels", help="hand-label CSV to check the judge against")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--out-dir", default="results/scores")
    args = ap.parse_args()

    run = json.loads(Path(args.run).read_text())
    key = json.loads(Path(args.key).read_text())
    findings = run["findings"]

    print(f"Scoring {args.run} ({len(findings)} findings) with judge {args.model} ...")
    t0 = time.time()
    verdicts = asyncio.run(score_all(key, findings, args.model, args.concurrency))
    elapsed = round(time.time() - t0, 1)

    for v in verdicts:
        ids = " ".join(v["matched_finding_ids"]) or "-"
        print(f"  {v['defect']} {v['verdict']:<7} {ids:<14} {v['reason']}")

    m = metrics(verdicts, key)
    matched = {i for v in verdicts for i in v["matched_finding_ids"]}
    m["findings"] = len(findings)
    m["findings_matching_a_defect"] = len(matched)
    print(f"\nRecall {m['hits']}/{m['defects']} = {m['recall']:.0%} | "
          f"incl. partial {m['recall_incl_partial']:.0%} | "
          f"severity-weighted {m['severity_weighted_recall']:.0%} | "
          f"{m['findings_matching_a_defect']}/{m['findings']} findings match a planted defect | {elapsed}s")

    report = {"run": args.run, "method": run.get("method", "baseline"),
              "review_model": run.get("model"), "judge_model": args.model,
              "metrics": m, "verdicts": verdicts}
    if args.labels:
        report["judge_vs_labels"] = compare_with_labels(verdicts, args.labels)
        
    out = Path(args.out_dir) / f"{Path(args.run).stem}__judge-{args.model}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()