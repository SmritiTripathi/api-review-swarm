"""Shared helpers for calling Claude with structured JSON output."""
import json
import time

import anthropic
from dotenv import load_dotenv

load_dotenv()

DEV_MODEL = "claude-haiku-5-5"      # cheap: for building and debugging
FINAL_MODEL = "claude-sonnet-5-5"   # for the runs you will report

client = anthropic.Anthropic()

# Models that reject forced tool use (tool_choice "tool"/"any") with a 400.
# Source: Anthropic docs, "Define tools" > "Forcing tool use".
NO_FORCED_TOOL = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"}

CATEGORIES = ["naming", "errors", "documentation", "security", "schema-design",
              "pagination", "consistency", "idempotency", "other"]

# A "tool" here is just a JSON schema. Forcing the model to call it guarantees
# structured output we can parse reliably, with no regex on free text.
FINDINGS_TOOL = {
    "name": "report_findings",
    "description": "Report all developer-experience issues found in the API spec.",
    "input_schema": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string",
                                  "description": "Short summary, under 12 words"},
                        "location": {"type": "string",
                                     "description": "Exact location, e.g. 'GET /developer/v1/cards/physical' or 'components.schemas.Foo.properties.bar'"},
                        "category": {"type": "string", "enum": CATEGORIES},
                        "severity": {"type": "integer", "enum": [1, 2, 3],
                                     "description": "1 minor, 2 moderate, 3 serious"},
                        "evidence": {"type": "string",
                                     "description": "What in the spec shows this problem"},
                        "suggested_fix": {"type": "string"},
                    },
                    "required": ["title", "location", "category", "severity",
                                 "evidence", "suggested_fix"],
                },
            }
        },
        "required": ["findings"],
    },
}


def load_spec_text(path):
    """Load a spec as compact JSON (no indentation) to save tokens."""
    with open(path) as f:
        return json.dumps(json.load(f), separators=(",", ":"))


def strict_schema(schema):
    """Return a copy of a JSON schema with additionalProperties: false on every
    object. Structured outputs require this; tool schemas do not."""
    if isinstance(schema, dict):
        out = {k: strict_schema(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(schema, list):
        return [strict_schema(v) for v in schema]
    return schema


def call_structured(model, system, user, tool, max_tokens=16000, retries=4):
    """Call Claude and get JSON matching tool["input_schema"].
    Returns (result, usage, stop_reason).

    Two ways to get structured output, depending on the model:
      - forced tool use: the model must "call" our tool; its input is the JSON
      - structured outputs: the API constrains the reply text to our schema
    """
    kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": user}])
    use_structured_outputs = model in NO_FORCED_TOOL
    if use_structured_outputs:
        kwargs["output_config"] = {"format": {
            "type": "json_schema", "schema": strict_schema(tool["input_schema"])}}
    else:
        kwargs["tools"] = [tool]
        kwargs["tool_choice"] = {"type": "tool", "name": tool["name"]}

    for attempt in range(retries):
        try:
            resp = client.messages.create(**kwargs)
            break
        except (anthropic.RateLimitError, anthropic.APIConnectionError,
                anthropic.InternalServerError) as e:
            wait = 2 ** (attempt + 1)
            print(f"  transient error ({type(e).__name__}); retrying in {wait}s")
            time.sleep(wait)
    else:
        raise RuntimeError("API call failed after retries")

    if resp.stop_reason == "max_tokens":
        raise RuntimeError(
            "Output hit max_tokens and was cut off. Raise max_tokens or ask for shorter output.")

    if use_structured_outputs:
        # Skip any thinking blocks; the JSON is in the text block(s).
        text = "".join(b.text for b in resp.content if b.type == "text")
        if not text:
            raise RuntimeError(f"No text returned (stop_reason={resp.stop_reason})")
        result = json.loads(text)
    else:
        block = next((b for b in resp.content if b.type == "tool_use"), None)
        if block is None:
            raise RuntimeError(f"No structured output returned (stop_reason={resp.stop_reason})")
        result = block.input

    usage = {"input_tokens": resp.usage.input_tokens,
             "output_tokens": resp.usage.output_tokens}
    return result, usage, resp.stop_reason