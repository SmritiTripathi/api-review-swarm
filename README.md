# API Review Swarm

An evaluation harness that measures whether a **multi-agent persona swarm** finds more real defects in an OpenAPI spec than a **single-pass LLM review**, and at what cost in tokens and latency.

> **Status:** in progress. The benchmark, baseline reviewer, persona generation and parallel swarm are working, and an LLM judge scores every run against the answer key. Repeated runs, the same-budget baseline and Sonnet reviews are next.

## Highlights

- **Ground-truth benchmark:** 19 defects across 7 categories, planted deterministically into a real public API spec, with a machine-readable answer key. Every review can be scored for recall instead of judged by eye.
- **Multi-agent review:** six persona reviewers (security engineer, AI-agent builder, API architect, …) run in parallel, and their findings are merged and de-duplicated.
- **Cost-controlled comparison:** the swarm is compared with a single review *and* with a same-budget baseline (the single review repeated N times), which separates the value of different perspectives from the value of more attempts.
- **Prompt caching for parallel agents:** the shared spec is cached once and reused by every persona, so six reviewers cost far less than six times one review (see [Results](#results)).
- **Model-agnostic, schema-enforced output:** one adapter returns schema-valid JSON using strict tool use or native structured outputs, whichever the model supports.
- **Fault isolation:** a failed or malformed reviewer is recorded and flagged; it never discards the other reviewers' work.
- **Reproducible:** fixed seeds, a pinned spec snapshot, and one JSON record per run with findings, token usage and latency.

## Architecture

```
spec + planted defects ─┬─► Baseline: 1 general reviewer ────────────┐
                        └─► Swarm: N persona reviewers ─► aggregator ─┤
                                                                      ▼
                                        Scorer: findings ↔ answer key
                                                                      ▼
                          recall · severity-weighted recall · tokens · latency

```


| Component      | File                   | Role                                                                         |
| -------------- | ---------------------- | ---------------------------------------------------------------------------- |
| Spec slicer    | `src/slice_spec.py`    | Extracts a self-contained subset of paths, following `$ref`s transitively    |
| Defect planter | `src/plant_defects.py` | Applies 14 seeded mutators and writes the answer key                         |
| LLM adapter    | `src/llm.py`           | Returns schema-valid JSON on any supported Claude model, with retries        |
| Baseline       | `src/baseline.py`      | Single-pass senior-reviewer prompt                                           |
| Personas       | `src/personas.py`      | Generates distinct reviewer personas from seeded attributes                  |
| Swarm          | `src/swarm.py`         | Parallel persona reviews with a cached shared spec and per-reviewer failure isolation |
| Aggregator     | `src/aggregate.py`     | *Planned:* merges duplicate findings across reviewers                        |
| Scorer         | `src/score.py`         | Location filter plus an LLM judge (Sonnet 5.5), validated against hand labels |


## Benchmark

A slice of Ramp's public Developer API (13 paths, 18 operations: cards, transactions, reimbursements) with 19 planted defects.


| Category        | Defects | Example                                               |
| --------------- | ------- | ----------------------------------------------------- |
| errors          | 4       | GET returns `201 Created`; error responses removed    |
| schema-design   | 4       | money amount typed as string; enum constraint removed |
| naming          | 3       | camelCase parameter in a snake_case API               |
| documentation   | 3       | GET summarised as "Permanently delete this resource"  |
| agent-readiness | 2       | parameter described only as "Filter."                 |
| security        | 2       | write endpoint with no auth; full card number exposed |
| pagination      | 1       | list endpoint without paging parameters               |


## Results

All runs are scored by the same judge (`claude-sonnet-5-5`), which agreed with hand labels on 18 of 19 defects. Token and latency figures come from the run files listed; scores from the matching files in `results/scores/`. Each row is a **single run**: repeated runs are next.

| Method | Model | Findings (raw) | Recall | Severity-weighted recall | Total input tokens | Cached share | Billed-input equivalent¹ | Output tokens | Latency | Run file |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|---|
| Baseline | claude-haiku-5-5 | 33 | 14/19 (74%) | 71% | 71,214 | 0% | 71,214 | 5,478 | 22.2 s | `baseline_claude-haiku-5-5_20261116-114835.json` |
| Swarm (6 personas) | claude-haiku-5-5 | 87 | 14/19 (74%) | 79% | 428,841 | 99% | 128,162 | 18,100 | 29.2 s | `swarm_claude-haiku-5-5_20261116-130554.json` |
| Same-budget baseline | claude-haiku-5-5 | | | | | | | | | not yet run |
| Baseline | claude-sonnet-5-5 | | | | | | | | | not yet run |
| Swarm (6 personas) | claude-sonnet-5-5 | | | | | | | | | not yet run |

¹ Input tokens weighted by their price relative to uncached input: cache writes ×1.25, cache reads ×0.1 (Haiku 5.5 rates from Anthropic's prompt-caching docs). It compares input cost across runs without depending on dollar prices.

**First observations (one run each):**
- **Same recall, different defects.** Both caught 14 of 19, but not the same 14. Only the swarm caught a full card number exposed in a response (severity 3, found by the security-engineer persona) and missing error responses on an unsuspend endpoint. Only the baseline caught two missing operation descriptions (severity 1). Together they caught 16 of 19.
- **The swarm caught the more serious defects:** severity-weighted recall 79% vs 71%.
- **Neither caught three schema defects** where something was *removed*: two `required` markers and an enum.
- **Cost:** the swarm used 1.8× the baseline's price-weighted input (caching saved 70%), 3.3× the output tokens and 1.3× the latency.
- **Duplication:** only 36 of the swarm's 87 findings match a planted defect, and many of those repeat each other (five personas reported the same camelCase parameter).
- **Not yet conclusive:** with one run each, a 2-defect difference may be noise. The same-budget baseline and repeated runs will show whether the personas, rather than extra attempts, explain the difference.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=sk-..." > .env

python src/plant_defects.py data/spec_clean.json data/spec_flawed.json data/answer_key.json
python src/baseline.py --model claude-haiku-5-5
python src/personas.py

```

Rebuilding the spec slice from the source, and the reasoning behind each design choice, are covered in [docs/notes.md](https://claude.ai/chat/docs/notes.md).

## Roadmap

- [x] Benchmark: spec slice, 19 planted defects, answer key
- [x] Baseline single-pass reviewer
- [x] Persona generation
- [x] Parallel swarm with prompt caching and failure isolation
- [ ] Aggregation: merge duplicate findings across reviewers
- [x] Scorer, with the LLM judge validated against hand-labelled matches (18/19 agreement)
- [ ] Experiments across models, with repeated runs
- [ ] Extensions: grounding check on finding locations; tool-using reviewers; prompt-injection robustness

## Data source

Uses a snapshot (see `data/SNAPSHOT_DATE.txt`) of Ramp's publicly published [Developer API spec](https://docs.ramp.com/openapi/developer-api.json) for research and demonstration only. This project is independent of and not endorsed by Ramp. Findings illustrate the review technique and are not a statement about the quality of Ramp's API.