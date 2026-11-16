# API Review Swarm

An evaluation harness that measures whether a **multi-agent persona swarm** finds more real defects in an OpenAPI spec than a **single-pass LLM review**, and at what cost in tokens and latency.

> **Status:** in progress. The benchmark and baseline reviewer are complete; the swarm, scorer and experiments are under development.

## Highlights

- **Ground-truth benchmark:** 19 defects across 7 categories, planted deterministically into a real public API spec, with a machine-readable answer key. Every review can be scored for recall instead of judged by eye.
- **Multi-agent review:** six persona reviewers (security engineer, AI-agent builder, API architect, …) run in parallel, and their findings are merged and de-duplicated.
- **Cost-controlled comparison:** the swarm is compared with a single review *and* with a same-budget baseline (the single review repeated N times), which separates the value of different perspectives from the value of more attempts.
- **Model-agnostic structured output:** one adapter returns schema-valid JSON using forced tool use or native structured outputs, whichever the model supports.
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
| Swarm          | `src/swarm.py`         | *Planned:* parallel persona reviews and aggregation                          |
| Scorer         | `src/score.py`         | *Planned:* location matching plus an LLM judge validated against hand labels |


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

*Pending.* Each row will be filled from the run files in `results/`, using the same metrics for every model and method.


| Method               | Model             | Recall | Severity-weighted recall | Input tokens | Output tokens | Latency |
| -------------------- | ----------------- | ------ | ------------------------ | ------------ | ------------- | ------- |
| Baseline             | claude-haiku-5-5  |        |                          |              |               |         |
| Baseline             | claude-sonnet-5-5 |        |                          |              |               |         |
| Swarm                | claude-haiku-5-5  |        |                          |              |               |         |
| Swarm                | claude-sonnet-5-5 |        |                          |              |               |         |
| Same-budget baseline | claude-sonnet-5-5 |        |                          |              |               |         |


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
- [ ] Persona generation
- [ ] Parallel swarm with prompt caching and finding aggregation
- [ ] Scorer, with the LLM judge validated against hand-labelled matches
- [ ] Experiments across models, with repeated runs
- [ ] Extensions: grounding check on finding locations; tool-using reviewers; prompt-injection robustness

## Data source

Uses a snapshot (see `data/SNAPSHOT_DATE.txt`) of Ramp's publicly published [Developer API spec](https://docs.ramp.com/openapi/developer-api.json) for research and demonstration only. This project is independent of and not endorsed by Ramp. Findings illustrate the review technique and are not a statement about the quality of Ramp's API.