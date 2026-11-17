"""Run every method several times, score each run with the same judge, summarise.

Methods:
  baseline  one generalist reviewer, one call
  swarm     six persona reviewers in parallel (data/personas.json)
  control   six *identical* generalist reviewers in parallel: same code, same
            caching, same rules and cap as the swarm. The only difference from
            the swarm is that every reviewer has the same prompt. If the swarm
            beats the control, the personas help; if not, the gain came from
            making more attempts.

Usage:
  python src/experiment.py --repeats 3
"""
import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

from baseline import SYSTEM as GENERIC_PROMPT
from llm import DEV_MODEL

METHODS = ["baseline", "swarm", "control"]


def make_control_personas(path, n=6):
    """Six copies of the baseline's generalist prompt, in the personas format."""
    personas = [{"id": f"G{i}", "name": "Generalist", "system_prompt": GENERIC_PROMPT}
                for i in range(1, n + 1)]
    path.write_text(json.dumps({"note": "control: identical generalist reviewers",
                                "personas": personas}, indent=2))


def run(cmd):
    """Run another script of this project; stop the experiment if it fails."""
    print("\n$ " + " ".join(cmd))
    subprocess.run([sys.executable, *cmd], check=True)


def review(method, model, out, control_personas):
    if method == "baseline":
        run(["src/baseline.py", "--model", model, "--out", str(out)])
    elif method == "swarm":
        run(["src/swarm.py", "--model", model, "--out", str(out)])
    else:
        run(["src/swarm.py", "--model", model, "--out", str(out),
             "--personas", str(control_personas), "--method", "control"])


def cold_cache_input(r):
    """Input cost in uncached-token equivalents, as if the run started with an
    empty cache: one cache write (x1.25), then reads (x0.1) for the other
    reviewers. Haiku 5.5 rates.

    Measured costs depend on run order: a run that starts within 5 minutes of
    another run with the same spec reads that run's cache and skips the write.
    Recomputing every run from a cold cache keeps methods comparable."""
    u = r["usage"]
    reviews = r.get("reviews")
    if not reviews:   # baseline: one call, nothing cached
        return u["input_tokens"]
    n = len(reviews)
    cached_per_call = (u.get("cache_write_tokens", 0) + u.get("cache_read_tokens", 0)) / n
    return u["input_tokens"] + 1.25 * cached_per_call + 0.1 * cached_per_call * (n - 1)


def summarise(exp_dir, key, judge):
    rows, caught = [], {m: {d["id"]: 0 for d in key} for m in METHODS}
    for run_file in sorted(exp_dir.glob("*_r*.json")):
        method = run_file.stem.split("_r")[0]
        r = json.loads(run_file.read_text())
        s = json.loads((exp_dir / "scores" / f"{run_file.stem}__judge-{judge}.json").read_text())
        for v in s["verdicts"]:
            if v["verdict"] == "hit":
                caught[method][v["defect"]] += 1
        rows.append({"method": method, "run": run_file.name,
                     "recall": s["metrics"]["recall"],
                     "sev_recall": s["metrics"]["severity_weighted_recall"],
                     "findings": len(r["findings"]),
                     "input_cost": cold_cache_input(r),
                     "output_tokens": r["usage"]["output_tokens"],
                     "seconds": r["seconds"],
                     "failed": r.get("failed", [])})

    def stats(vals):
        return {"mean": round(statistics.mean(vals), 3), "min": min(vals), "max": max(vals)}

    summary = {}
    for m in METHODS:
        mr = [x for x in rows if x["method"] == m]
        if not mr:
            continue
        summary[m] = {"runs": len(mr),
                      **{k: stats([x[k] for x in mr]) for k in
                         ["recall", "sev_recall", "findings", "input_cost",
                          "output_tokens", "seconds"]},
                      "incomplete_runs": sum(1 for x in mr if x["failed"])}
    return rows, summary, caught


def print_summary(summary, caught, key, repeats):
    print("\n## Summary (mean, with min-max across runs)\n")
    print("| Method | Runs | Recall | Severity-weighted recall | Findings | Input cost (cold cache)¹ | Output tokens | Latency |")
    print("|---|--:|--:|--:|--:|--:|--:|--:|")
    for m, s in summary.items():
        pct = lambda k: f"{s[k]['mean']:.0%} ({s[k]['min']:.0%}-{s[k]['max']:.0%})"
        num = lambda k: f"{s[k]['mean']:,.0f}"
        print(f"| {m} | {s['runs']} | {pct('recall')} | {pct('sev_recall')} | {num('findings')} "
              f"| {num('input_cost')} | {num('output_tokens')} | {s['seconds']['mean']:.1f} s |")
        if s["incomplete_runs"]:
            print(f"  WARNING: {s['incomplete_runs']} {m} run(s) had a failed reviewer")
    print("\n¹ Uncached-input-token equivalents, recomputed as if each run started "
          "with an empty cache (writes x1.25, reads x0.1).")

    print(f"\n## How often each defect was caught (out of {repeats} runs)\n")
    print("| Defect | Category | Sev | " + " | ".join(m for m in summary) + " |")
    print("|---|---|--:|" + "--:|" * len(summary))
    for d in key:
        counts = " | ".join(str(caught[m][d["id"]]) for m in summary)
        print(f"| {d['id']} | {d['category']} | {d['severity']} | {counts} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--model", default=DEV_MODEL, help="review model")
    ap.add_argument("--judge", default="claude-sonnet-5-5")
    ap.add_argument("--methods", nargs="+", default=METHODS, choices=METHODS)
    ap.add_argument("--summarise-only", metavar="EXP_DIR",
                    help="skip the runs; re-summarise an existing experiment folder")
    args = ap.parse_args()
    key = json.loads(Path("data/answer_key.json").read_text())

    if args.summarise_only:
        exp_dir = Path(args.summarise_only)
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        exp_dir = Path(f"results/experiments/{stamp}_{args.model}")
        (exp_dir / "scores").mkdir(parents=True)
        control_personas = exp_dir / "personas_control.json"
        make_control_personas(control_personas)

        # Interleave methods (baseline, swarm, control, baseline, ...) so that
        # any drift over time, e.g. API load, affects every method equally.
        for r in range(1, args.repeats + 1):
            for m in args.methods:
                out = exp_dir / f"{m}_r{r}.json"
                review(m, args.model, out, control_personas)
                run(["src/score.py", str(out), "--model", args.judge,
                     "--out-dir", str(exp_dir / "scores")])

    rows, summary, caught = summarise(exp_dir, key, args.judge)
    (exp_dir / "summary.json").write_text(json.dumps(
        {"model": args.model, "judge": args.judge, "summary": summary,
         "caught_counts": caught, "runs": rows}, indent=2))
    repeats = max(s["runs"] for s in summary.values())
    print_summary(summary, caught, key, repeats)
    print(f"\nSaved to {exp_dir}/summary.json")


if __name__ == "__main__":
    main()
