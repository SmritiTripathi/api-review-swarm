# Project notes (learning log)

Personal working notes: the reasoning behind each design choice, written as I build. The public summary is in the [README](../README.md). Measured numbers here are from individual development runs and may be out of date; reported results live only in the README's Results table.

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

- **Forced tool use** (Haiku): the request defines a "tool" whose input schema is the desired JSON shape and forces the model to call it. The tool does nothing; it exists only for its schema. The tool is marked `strict: true` so the API also *enforces* the schema (see Lessons: forcing the call alone does not).
- **Structured outputs** (Sonnet 5.5, Opus 5.5, Fable 5.2): these models reject forced tool use with a 400 error. Instead, the JSON schema is passed in `output_config`, and the API constrains the reply to match it.

Both methods need `"additionalProperties": false` on every object; `strict_schema()` adds it automatically, so the schemas themselves stay readable.

The rest of the code calls `call_structured()` without knowing which method ran underneath, so a provider-side change affects only one file.

### Swarm design: caching and parallelism (`src/swarm.py`)

- **Same rules as the baseline.** The review instructions are copied from `baseline.py`, so the persona is the main variable. The only deliberate difference: at most 15 findings per persona (baseline: 30), because each persona is narrower. Configurable with `--max-findings`.
- **Prompt caching.** All six personas send the same ~71k-token spec. The API can cache a request *prefix* and charge about a tenth of the normal input price to reuse it. The prefix order is tools → system → messages, so the system prompt is two blocks: (1) the spec, identical for everyone and marked `cache_control`; (2) the persona's own prompt. If the persona came first, every request would start differently and nothing would be reused.
- **Warm the cache, then go parallel.** A cache entry exists only once the first response has started. So persona 1 runs alone (writes the cache), then personas 2–6 run in parallel (read it). Total time is about two calls, in exchange for paying for the spec once instead of six times.
- **Concurrency.** `asyncio.to_thread` runs the ordinary blocking `call_structured` in background threads; `asyncio.gather` starts them together and returns results in the order given, even though they finish in a different order (which is why the console lines print out of order).
- **Fault isolation.** Each persona's call is wrapped in `try/except`. A failure is printed, stored in the results file under `failed`, and the run is flagged as incomplete, instead of crashing and losing every other persona's work. Non-object findings are dropped and counted.
- **Token accounting.** With caching, the API splits input three ways: `input_tokens` (uncached only), `cache_creation_input_tokens` (written) and `cache_read_input_tokens` (read). Total input is the sum. `llm.py` records all three; reporting `input_tokens` alone would make the swarm look almost free.



### Scoring rubric

Used for hand labels (`data/hand_labels_baseline.csv`) and, word for word, in the LLM judge's prompt.

- **hit:** the finding identifies the planted problem at the right location. Category label, wording and extra detail do not matter. The problem may be stated in the title or the evidence, as long as it is stated as a problem; it still counts if the finding's main topic is a different problem.
- **partial:** right location, but the planted problem is only implied, or a different problem is described there.
- **miss:** no finding describes the planted problem.
- A defect can be matched by several findings; one finding can match several defects.

**How the labels were made:** I labelled the 2026-11-16 Haiku baseline run, then compared my labels with a second, independent pass. We first agreed on 13 of 19. Four disagreements were rubric questions (category label, extra detail, problem stated only in evidence), which the rubric above settles. Two were findings I missed because they were buried at the end of findings about other problems (B21, B23). Final labels: 15 hits, 4 misses.

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
- [x] **4. Baseline reviewer:** one general prompt returning structured findings
- [x] **5. Personas:** 6 reviewer personas generated with Sonnet 5.5 (`data/personas.json`)
- [x] **6. Swarm:** persona reviewers in parallel, with prompt caching and failure isolation
- [ ] **7. Aggregation:** merge duplicate findings across personas
- [ ] **8. Scorer:** match findings to the answer key (location filter plus an LLM judge checked against hand labels); report recall, severity-weighted recall and unmatched findings
- [ ] **9. Experiments:** baseline vs swarm vs same-budget baseline, repeated runs, on Haiku and Sonnet
- [ ] **10. Results:** final tables and charts in the README, plus failure analysis

Stretch ideas:

- **Grounding check:** reject findings whose `location` doesn't exist in the spec, and report how often that happens.
- **Tool-using reviewers:** give agents `list_operations` / `get_schema` tools instead of the whole spec, and compare quality, tokens and latency.
- **Prompt-injection defect:** plant an instruction inside a description and check whether reviewers stay on task.



## Lessons so far

- **Token estimates:** characters ÷ 4 underestimates JSON. The slice was estimated at ~43k tokens but the API reported ~71k on the first Haiku run (2026-10-23). Use the API's `usage` numbers for cost.
- **Truncated JSON fails silently:** the first baseline run hit `max_tokens` partway through its JSON and returned 0 findings. `call_structured()` now raises an error when output is cut off.
- **Forcing a tool call does not validate its input:** in the first swarm run, one persona returned a finding as a plain string instead of an object, and the code crashed. Forced tool use guarantees the model *calls* the tool, not that the input matches the schema. Fix: `strict: true` on the tool, which makes the API enforce the schema.
- **One failure should not erase the rest:** the same crash discarded five good reviews, because `asyncio.gather` stops at the first exception and the save step never ran. Fix: catch errors per persona, record them, and mark the run incomplete.
- **Provider capabilities change:** Sonnet 5.5, Opus 5.5 and Fable 5.2 reject forced tool use (HTTP 400). Keeping all model-specific code in `llm.py` meant the fix touched one file.
- **Caching changed the cost picture:** the first Haiku swarm run (2026-11-16) sent 6× the baseline's input, but 99% of it was cached, so its price-weighted input was 1.8× the baseline's, at 1.3× the latency. Figures: README Results table.
- **Hand labels need a written rubric and a second pass:** my first labels and a second labeller agreed on 13 of 19 defects. The fixes: a written rubric for judgment calls, and a location filter so findings buried in other findings are not overlooked.
- **The baseline misses schema-level absences:** on the 2026-11-16 Haiku baseline, all 4 misses were in `components.schemas`, and 3 of them were something removed (a `required` marker, an enum). Reviewers spot what is wrong on the page more easily than what is missing.
- **Not every extra finding is a false positive:** the baseline reported ~15 issues that weren't planted, and many may be real issues in the original spec. Running the baseline on `spec_clean.json` measures that background rate.
- **Judges read instructions literally:** the first judge run agreed with my labels on 15 of 19. It treated the exact removed item ('400') as required, and down-weighted problems mentioned outside a finding's main point. Two general clarifications to the prompt fixed both, without mentioning any specific defect, to avoid tuning the judge to my 19 labels.


## Talking points

Short answers I can expand on, each tied to something I built and measured.

1. **The evaluation problem.** "LLM review output is easy to demo and hard to measure. I planted 19 known defects in a real API spec so every review gets a recall score against an answer key, instead of being judged by eye."
2. **A fair comparison.** "Six reviewers spend six times the tokens, so beating one reviewer proves little. I added a same-budget baseline, one reviewer run six times, to separate the value of different perspectives from the value of more attempts."
3. **Cost and latency engineering.** "The personas share a 71k-token spec. I put it first in the prompt so it forms a cacheable prefix, ran one persona to warm the cache, then the rest in parallel. 99% of input was cached: the swarm cost 1.8× the baseline's input instead of 6×, at 1.3× the latency."
4. **Reliability in multi-agent systems.** "One agent returned malformed JSON and crashed the run, losing five good reviews. I fixed the cause with schema enforcement (strict tool use) and the blast radius with per-agent failure isolation, so failures are recorded and flagged rather than silent or fatal."
5. **Adapting to model differences.** "Newer models dropped support for forced tool use. Because all model-specific logic lived in one adapter, switching those models to native structured outputs was a one-file change."
6. **Honest metrics.** "With caching, the API's `input_tokens` field covers only uncached tokens. Reporting it alone would have made the swarm look almost free, so I track all three input counts and a price-weighted total."
7. **Reproducibility.** "Fixed seeds for defects and personas, a pinned spec snapshot, deterministic slicing, and one JSON file per run with findings, tokens and timing."
8. **Interpreting a tie honestly.** "My first comparison was 14 of 19 each, but on different defects: the security persona caught the most serious one, which the generalist missed, and the generalist caught two minor documentation gaps no persona covered. I reported it as inconclusive until repeated runs and a same-budget control, rather than claiming a win."



## Git workflow used in this project

- `git status`: what changed and what is untracked, before every commit
- `git diff [file]`: review changes to tracked files (press `q` to exit)
- `git add <files>`: stage specific files instead of `git add .`
- `git commit -m "..."`: one logical change per commit, with a message saying what and why
- `git checkout -b <branch>`: start a feature branch from the current branch
- `git push -u origin <branch>`: first push of a new branch; links local and GitHub branches so later a plain `git push` works
- Pull request on GitHub, then `git checkout main && git pull`: merge finished work into `main` and bring it back to the local copy

