# API Review Swarm

A multi-persona LLM system that reviews OpenAPI specs for developer-experience
problems, and measures whether a persona swarm finds more real issues than a
single-pass review.

> Work in progress. Results and architecture details will be added as the
> project is built.

## Data source

The experiments use a slice of Ramp's publicly published Developer API
OpenAPI spec.

- Source: https://docs.ramp.com/openapi/developer-api.json
- Snapshot date: see `data/SNAPSHOT_DATE.txt`
- The spec is used here for research and demonstration only. This project is
  independent and does not imply any endorsement by or affiliation with Ramp.
- Findings are illustrative of the review technique, not a statement about the
  quality of Ramp's API.

## Reproducing the spec slice

```bash
curl -L -o data/developer-api.json https://docs.ramp.com/openapi/developer-api.json
python src/slice_spec.py data/developer-api.json data/spec_clean.json \
  /developer/v1/cards /developer/v1/transactions
```
