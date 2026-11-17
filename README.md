# API Review Swarm

An evaluation harness that measures whether a **multi-agent persona swarm** finds more real defects in an OpenAPI spec than a **single-pass LLM review**, and at what cost in tokens and latency.

> **Status:** in progress. The benchmark, baseline, swarm, judge-based scorer and a controlled Haiku experiment (3 repeats per method) are complete. Next: Sonnet reviewers, a persona aimed at the defects no method catches, and finding aggregation.

## Highlights

- **Ground-truth benchmark:** 19 defects across 7 categories, planted deterministically into a real public API spec, with a machine-readable answer key. Every review can be scored for recall instead of judged by eye.
- **Multi-agent review:** six persona reviewers (security engineer, AI-agent builder, API architect, …) run in parallel against the same spec.
- **Controlled experiment:** the swarm is compared with a single review *and* with a control of six identical generalist reviewers using the same code, caching and cost. Only reviewer diversity differs, which separates the value of different perspectives from the value of more attempts.
- **LLM-as-judge, validated:** a location filter plus a Sonnet judge score every run against the answer key; the judge agrees with independent hand labels on 18 of 19 defects.
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
| Experiment     | `src/experiment.py`    | Repeated, interleaved runs of each method, scored by one judge, summarised with ranges |


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

**Experiment:** Haiku 5.5 reviewers, 3 runs per method, run in interleaved order, every run scored by the same Sonnet 5.5 judge. Data: `results/experiments/20261117-130543_claude-haiku-5-5/`.

| Method | Recall, mean (range) | Severity-weighted recall | Findings per run | Findings matching a planted defect | Input cost¹ | Output tokens | Latency |
|---|--:|--:|--:|--:|--:|--:|--:|
| Baseline: 1 generalist | 68% (63–74%) | 70% (66–76%) | 32 | 41% | 71,234 (1.0×) | 5,099 | 21.6 s |
| Swarm: 6 personas | 72% (68–74%) | 76% (71–79%) | 88 | 41% | 128,162 (1.8×) | 18,379 | 28.2 s |
| Control: 6 identical generalists | 70% (63–79%) | 75% (68–82%) | 84 | 58% | 126,473 (1.8×) | 15,498 | 25.6 s |

¹ Uncached-input-token equivalents, recomputed as if each run started with an empty cache (cache writes ×1.25, reads ×0.1, Haiku 5.5 rates from Anthropic's prompt-caching docs).

**Defects caught, out of 3 runs each** (only defects where the methods differ, plus those never caught):

| Defect | Severity | Baseline | Swarm | Control |
|---|--:|--:|--:|--:|
| D04 unsuspend endpoint has no error responses | 2 | 1 | 3 | 2 |
| D06, D07 operations missing descriptions | 1 | 1, 1 | 0, 0 | 1, 0 |
| D08, D09 parameters described only as "Data." | 2 | 3, 3 | 3, 3 | 2, 2 |
| D18 full card number exposed in a response | 3 | 1 | 2 | 3 |
| D14, D15, D16 `required` marker or enum removed | 2 | 0 | 0 | 0 |

**Findings:**
- **Persona diversity did not measurably improve recall on Haiku.** The swarm and the control differ by about one defect on average, well inside the run-to-run range (a single method varies by up to 16 points). Six reviewers of either kind edge out one reviewer by a similar margin, which is also within the range.
- **An earlier single-run result did not replicate.** In the first comparison only the security persona caught D18 (full card number exposed). Across repeats, six identical generalists caught it in 3 of 3 runs. Detecting it comes from having more reviewers, not specialist ones.
- **The swarm has a consistent blind spot:** missing operation descriptions (D06, D07) were caught in 0 of 6 swarm attempts. No persona owns basic documentation completeness.
- **Generalists converge; personas spread out.** 58% of the control's findings target a planted defect, against 41% of the swarm's. Persona findings cover more issues outside the answer key, which this benchmark cannot credit or penalise.
- **Removed constraints are never caught.** D14–D16 were caught in 0 of 27 runs across all methods.
- **Cost:** both six-reviewer methods cost 1.8× the baseline's input thanks to caching (6× without), with 3–3.6× the output tokens and 1.2–1.3× the latency.

Earlier single runs and judge-validation scores remain in `results/` and `results/scores/`.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=sk-..." > .env

python src/plant_defects.py data/spec_clean.json data/spec_flawed.json data/answer_key.json
python src/baseline.py --model claude-haiku-5-5
python src/personas.py

```

Rebuilding the spec slice from the source, and the reasoning behind each design choice, are covered in [docs/notes.md](docs/notes.md).

## Roadmap

- [x] Benchmark: spec slice, 19 planted defects, answer key
- [x] Baseline single-pass reviewer
- [x] Persona generation
- [x] Parallel swarm with prompt caching and failure isolation
- [ ] Aggregation: merge duplicate findings across reviewers
- [x] Scorer, with the LLM judge validated against hand-labelled matches (18/19 agreement)
- [x] Controlled experiment on Haiku: baseline vs swarm vs identical-reviewer control, 3 repeats
- [ ] Same experiment with Sonnet reviewers
- [ ] Targeted persona for the never-caught defects (removed `required` markers and enums)
- [ ] Extensions: grounding check on finding locations; tool-using reviewers; prompt-injection robustness

## Data source

Uses a snapshot (see `data/SNAPSHOT_DATE.txt`) of Ramp's publicly published [Developer API spec](https://docs.ramp.com/openapi/developer-api.json) for research and demonstration only. This project is independent of and not endorsed by Ramp. Findings illustrate the review technique and are not a statement about the quality of Ramp's API.