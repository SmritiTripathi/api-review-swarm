# Project notes (learning log)

Personal working notes: the reasoning behind each design choice, written as I build. The public summary is in the [README](https://claude.ai/README.md). Measured numbers here are from individual development runs and may be out of date; reported results live only in the README's Results table.

## Why this project exists

An OpenAPI spec describes every endpoint, parameter and response of a web API. People review specs for problems: confusing names, missing error documentation, security gaps. LLMs can review them too, but their output is hard to judge. If a model reports 34 problems, how many are real, and how many real ones did it miss?

To answer that, the project has two parts:

1. **A test with a known answer key.** Take a real spec, deliberately plant known defects in it, and record each one. Any review can then be scored: "found 14 of 19 known problems" instead of "found some stuff".
2. **A comparison of two review approaches** using that test.

## Approach

```
spec with planted defects
   ├─► Baseline: 1 reviewer ─────────────────────► findings ─┐
   └─► Swarm: 6 persona reviewers ─► merge ──────► findings ─┤
                                                             ▼
                              Scorer: compare findings with the answer key
                                                             ▼
                      how many found · how serious · cost · time taken

```

### The benchmark (ground truth)

- **Real spec:** a slice of Ramp's public Developer API (cards, transactions, reimbursements). It's real, so the review task is realistic, and the slice fits in one prompt (about 71k input tokens on the first Haiku run).
- **Planted defects:** `plant_defects.py` deliberately breaks the spec in 19 known ways, e.g. removing authentication from a write endpoint, renaming a parameter to camelCase, or adding a field that exposes full card numbers.
- **Answer key:** every planted defect is recorded in `answer_key.json` with its location, severity and a hint describing what a good finding says.

### Baseline: one general reviewer

A single LLM call: "You are a senior API reviewer; find the problems in this spec." This is the simplest reasonable approach and the yardstick. Anything more complex has to beat it to be worth its cost.

### Swarm: a team of persona reviewers

Several reviewers, each instructed to be a different kind of person: a first-day developer, a security engineer, a partner integrator, a finance-ops admin, an AI-agent builder and an API architect. They review the same spec in parallel, and their findings are merged and de-duplicated.

The personas are generated once by `personas.py` and saved, so every experiment uses the same reviewers. Each fixed role gets a different pet peeve, time or compliance constraint, and reading style, picked at random with a fixed seed. This keeps the reviewers distinct from each other and reproducible.

**Hypothesis:** different points of view catch different problems. The security engineer notices missing auth; the agent builder notices vague parameter descriptions.

**Fair comparison:** six reviewers cost about six times as much as one. So the swarm is compared not only with a single baseline run, but also with the baseline **run six times with the results combined**. If the swarm still wins, the personas are adding something beyond extra attempts.

### Model choices


| Purpose                                       | Model               | Why                                                                                                                   |
| --------------------------------------------- | ------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Building and debugging (`DEV_MODEL`)          | `claude-haiku-5-5`  | Each run sends the whole spec; while iterating, cheap and fast matters most                                           |
| Personas and reported results (`FINAL_MODEL`) | `claude-sonnet-5-5` | Personas are generated once and reused, so quality matters more than cost; reported numbers should use a strong model |


A later experiment: does the swarm help a weaker model more than a stronger one?

### Getting structured output from the model

The code needs every finding as JSON with fixed fields (`location`, `category`, `severity`, `evidence`, …), not free prose. `src/llm.py` handles this in one function, `call_structured()`, using one of two methods depending on the model:

- **Forced tool use** (Haiku): the request defines a "tool" whose input schema is the desired JSON shape and forces the model to call it. The tool does nothing; it exists only for its schema.
- **Structured outputs** (Sonnet 5.5, Opus 5.5, Fable 5.2): these models reject forced tool use with a 400 error. Instead, the JSON schema is passed in `output_config`, and the API constrains the reply to match it.

The rest of the code calls `call_structured()` without knowing which method ran underneath, so a provider-side change affects only one file.

## Seeded-defect benchmark

`src/plant_defects.py` applies 14 deterministic mutators (fixed seed `42`) to the clean slice and writes a 19-item answer key.


| Category        | Count | Examples                                                              |
| --------------- | ----- | --------------------------------------------------------------------- |
| errors          | 4     | error responses removed, GET returning `201`, unstructured error body |
| schema-design   | 4     | required field dropped, enum removed, money amount typed as string    |
| naming          | 3     | camelCase query params, singular path among plural siblings           |
| documentation   | 3     | missing descriptions, GET summarised as "Permanently delete"          |
| agent-readiness | 2     | vague parameter descriptions ("Filter.", "Value.")                    |
| security        | 2     | write endpoint with `security: []`, `full_card_number` exposed        |
| pagination      | 1     | list endpoint with paging params removed                              |


## Repository layout

```
src/
  hello.py            # SDK smoke test
  parallel_test.py    # asyncio fan-out smoke test
  inspect_spec.py     # summarise a spec: ops, tags, size estimate
  slice_spec.py       # extract a self-contained subset of paths (+ transitive $refs)
  plant_defects.py    # plant seeded defects, write answer key
  llm.py              # call_structured(): JSON output via forced tool or structured outputs
  baseline.py         # single-reviewer baseline
  personas.py         # generate reviewer personas from sampled attributes
data/
  developer-api.json  # raw spec snapshot
  SNAPSHOT_DATE.txt
  spec_clean.json     # 13 paths / 18 operations
  spec_flawed.json    # clean slice + planted defects
  answer_key.json     # ground truth for scoring
  personas.json       # generated reviewer personas
results/              # one JSON file per review run (findings, tokens, time)

```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=sk-..." > .env
python src/hello.py          # should print: setup works

```

## Reproducing

```bash
# 1. Benchmark
curl -L -o data/developer-api.json https://docs.ramp.com/openapi/developer-api.json
python src/inspect_spec.py data/developer-api.json
python src/slice_spec.py data/developer-api.json data/spec_clean.json \
  /developer/v1/cards /developer/v1/transactions /developer/v1/reimbursements
python src/plant_defects.py data/spec_clean.json data/spec_flawed.json data/answer_key.json

# 2. Baseline review (defaults to the cheap dev model)
python src/baseline.py
python src/baseline.py --model claude-sonnet-5-5

# 3. Personas (defaults to the final model)
python src/personas.py

```

Using the snapshot in `data/`, step 1 regenerates the committed benchmark. A fresh download may differ if Ramp has updated its spec since the snapshot date.

## Roadmap

- [x] **1. Setup:** venv, Anthropic SDK, sync and async smoke tests
- [x] **2. Data:** download, inspect and slice the Ramp spec
- [x] **3. Benchmark:** plant 19 seeded defects with an answer key
- [x] **4. Baseline reviewer:** one general prompt returning structured findings. First Haiku run: 34 findings, 71k in / 5k out tokens, 23 s
- [ ] **5. Personas:** generate 6 reviewer personas; `llm.py` now supports models without forced tool use
- [ ] **6. Swarm:** run the persona reviewers in parallel with prompt caching on the shared spec, then merge and de-duplicate findings
- [ ] **7. Scorer:** match findings to the answer key (location filter plus an LLM judge checked against hand labels); report recall, severity-weighted recall and unmatched findings
- [ ] **8. Experiments:** baseline vs swarm vs same-budget baseline (N combined single passes), repeated runs, tokens, cost and latency logged
- [ ] **9. Results:** tables and charts in the README, plus failure analysis

Stretch ideas:

- **Grounding check:** reject findings whose `location` doesn't exist in the spec, and report how often that happens.
- **Tool-using reviewers:** give agents `list_operations` / `get_schema` tools instead of the whole spec, and compare quality, tokens and latency.
- **Prompt-injection defect:** plant an instruction inside a description and check whether reviewers stay on task.

## Lessons so far

- **Token estimates:** characters ÷ 4 underestimates JSON. The slice was estimated at ~43k tokens but the API reported ~71k on the first Haiku run (2026-10-23). Use the API's `usage` numbers for cost.
- **Truncated JSON fails silently:** the first baseline run hit `max_tokens` partway through its JSON and returned 0 findings. `call_structured()` now raises an error when output is cut off.
- **Not every extra finding is a false positive:** the baseline reported ~15 issues that weren't planted, and many may be real issues in the original spec. Running the baseline on `spec_clean.json` measures that background rate.

