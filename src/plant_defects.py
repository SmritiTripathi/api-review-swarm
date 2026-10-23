"""Plant known defects in an OpenAPI spec and write an answer key."""
import copy
import json
import random
import sys
from collections import Counter

SEED = 42
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
WRITE_METHODS = {"post", "put", "patch", "delete"}
PAGINATION_PARAMS = {"page_size", "start", "limit", "cursor", "page", "offset"}
VAGUE = ["The id.", "Filter.", "Value.", "See docs.", "Data."]


# ---------- helpers ----------
def resolve(spec, ref):
    node = spec
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def iter_ops(spec):
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method in HTTP_METHODS:
                yield path, method, op


def peek(spec, p):
    """Read a parameter, following a $ref if needed (without changing anything)."""
    return resolve(spec, p["$ref"]) if "$ref" in p else p


def local_param(spec, op, i):
    """Return parameter i as an inline dict. A shared $ref is copied inline first,
    so editing it affects only this operation."""
    p = op["parameters"][i]
    if "$ref" in p:
        p = copy.deepcopy(resolve(spec, p["$ref"]))
        op["parameters"][i] = p
    return p


def param_candidates(spec, touched, pred):
    out = []
    for path, method, op in iter_ops(spec):
        if ("op", path, method) in touched:
            continue
        for i, p in enumerate(op.get("parameters", [])):
            if pred(peek(spec, p)):
                out.append((path, method, i))
    return out


def request_schema(spec, op):
    """Return (schema_name, schema) for a $ref'd JSON request body, else None."""
    body = op.get("requestBody")
    if not body:
        return None
    if "$ref" in body:
        body = resolve(spec, body["$ref"])
    schema = body.get("content", {}).get("application/json", {}).get("schema")
    if schema and "$ref" in schema:
        return schema["$ref"].split("/")[-1], resolve(spec, schema["$ref"])
    return None


def schema_props(spec):
    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        if isinstance(schema, dict):
            for prop, sub in schema.get("properties", {}).items():
                if isinstance(sub, dict):
                    yield name, prop, sub


def camel(name):
    head, *rest = name.split("_")
    return head + "".join(w.capitalize() for w in rest)


def defect(category, severity, location, what, why, hint):
    return {
        "category": category,
        "severity": severity,  # 1 = minor, 2 = moderate, 3 = serious
        "location": location,
        "what_changed": what,
        "why_it_matters": why,
        "detection_hint": hint,
    }


def where(method, path):
    return f"{method.upper()} {path}"


# ---------- mutators: each returns a defect record, or None if it can't apply ----------
def m_camel_case_param(spec, rng, touched):
    cands = param_candidates(
        spec, touched, lambda p: p.get("in") == "query" and "_" in p.get("name", ""))
    if not cands:
        return None
    path, method, i = rng.choice(cands)
    p = local_param(spec, spec["paths"][path][method], i)
    old, new = p["name"], camel(p["name"])
    p["name"] = new
    touched.add(("op", path, method))
    return defect("naming", 2, where(method, path),
                  f"Query parameter '{old}' renamed to '{new}' (camelCase), while the API uses snake_case elsewhere",
                  "Inconsistent naming makes the API harder to learn and easy to call incorrectly",
                  f"Should note that '{new}' uses camelCase, inconsistent with snake_case parameters elsewhere")


def m_remove_error_responses(spec, rng, touched):
    cands = []
    for path, method, op in iter_ops(spec):
        codes = [str(c) for c in op.get("responses", {})]
        if (("op", path, method) not in touched
                and any(c.startswith(("4", "5")) for c in codes)
                and any(c.startswith("2") for c in codes)):
            cands.append((path, method))
    if not cands:
        return None
    path, method = rng.choice(cands)
    responses = spec["paths"][path][method]["responses"]
    removed = [c for c in responses if str(c).startswith(("4", "5"))]
    for c in removed:
        del responses[c]
    touched.add(("op", path, method))
    return defect("errors", 2, where(method, path),
                  f"Removed documented error responses {removed}",
                  "Developers cannot tell what failures to expect or how to handle them",
                  "Should note that this operation documents no 4xx/5xx error responses")


def m_remove_op_description(spec, rng, touched):
    cands = [(p, m) for p, m, op in iter_ops(spec)
             if ("op", p, m) not in touched and len(op.get("description", "")) > 40]
    if not cands:
        return None
    path, method = rng.choice(cands)
    del spec["paths"][path][method]["description"]
    touched.add(("op", path, method))
    return defect("documentation", 1, where(method, path),
                  "Removed the operation's description",
                  "Behavior, constraints and side effects are no longer explained",
                  "Should note that this operation has no description or explanation beyond its summary")


def m_vague_param_description(spec, rng, touched):
    cands = param_candidates(spec, touched, lambda p: len(p.get("description", "")) > 30)
    if not cands:
        return None
    path, method, i = rng.choice(cands)
    p = local_param(spec, spec["paths"][path][method], i)
    new = rng.choice(VAGUE)
    old_name = p.get("name")
    p["description"] = new
    touched.add(("op", path, method))
    return defect("agent-readiness", 2, f"{where(method, path)} parameter '{old_name}'",
                  f"Replaced the parameter description with the vague text '{new}'",
                  "Humans and AI agents must guess what the parameter means and which values are valid",
                  f"Should note that parameter '{old_name}' has an uninformative description")


def m_remove_pagination(spec, rng, touched):
    cands = []
    for path, method, op in iter_ops(spec):
        if method == "get" and ("op", path, method) not in touched:
            names = [peek(spec, p).get("name") for p in op.get("parameters", [])]
            if any(n in PAGINATION_PARAMS for n in names):
                cands.append((path, method))
    if not cands:
        return None
    path, method = rng.choice(cands)
    op = spec["paths"][path][method]
    removed = [peek(spec, p).get("name") for p in op["parameters"]
               if peek(spec, p).get("name") in PAGINATION_PARAMS]
    op["parameters"] = [p for p in op["parameters"]
                        if peek(spec, p).get("name") not in PAGINATION_PARAMS]
    touched.add(("op", path, method))
    return defect("pagination", 2, where(method, path),
                  f"Removed pagination parameters {removed} from this list endpoint",
                  "Clients cannot page through large result sets, risking truncated or oversized responses",
                  "Should note that this list endpoint has no pagination or limit parameters")


def m_open_security(spec, rng, touched):
    cands = [(p, m) for p, m, op in iter_ops(spec)
             if m in WRITE_METHODS and op.get("security") != [] and ("op", p, m) not in touched]
    if not cands:
        return None
    path, method = rng.choice(cands)
    spec["paths"][path][method]["security"] = []
    touched.add(("op", path, method))
    return defect("security", 3, where(method, path),
                  "Set security to an empty list, declaring this write operation as requiring no authentication",
                  "A state-changing endpoint appears publicly callable",
                  "Should flag that this write operation declares no authentication or authorization")


def m_wrong_success_code(spec, rng, touched):
    cands = [(p, m) for p, m, op in iter_ops(spec)
             if m == "get" and "200" in op.get("responses", {}) and "201" not in op["responses"]
             and ("op", p, m) not in touched]
    if not cands:
        return None
    path, method = rng.choice(cands)
    responses = spec["paths"][path][method]["responses"]
    spec["paths"][path][method]["responses"] = {
        ("201" if str(k) == "200" else k): v for k, v in responses.items()}
    touched.add(("op", path, method))
    return defect("errors", 1, where(method, path),
                  "Changed this GET operation's success status code from 200 to 201 (Created)",
                  "Status codes should match HTTP semantics; 201 implies a resource was created",
                  "Should note that a read-only GET returns 201 Created, which is semantically wrong")


def m_error_schema_shape(spec, rng, touched):
    cands = []
    for path, method, op in iter_ops(spec):
        codes = [c for c in op.get("responses", {}) if str(c).startswith("4")]
        if codes and ("op", path, method) not in touched:
            cands.append((path, method, str(codes[0])))
    if not cands:
        return None
    path, method, code = rng.choice(cands)
    spec["paths"][path][method]["responses"][code] = {
        "description": "Error",
        "content": {"application/json": {"schema": {"type": "string"}}},
    }
    touched.add(("op", path, method))
    return defect("errors", 2, f"{where(method, path)} response {code}",
                  f"Replaced the {code} error response with a plain string body, unlike error shapes elsewhere",
                  "Inconsistent error formats force clients to special-case error handling",
                  f"Should note that the {code} response is an unstructured string, inconsistent with other errors")


def m_misleading_summary(spec, rng, touched):
    cands = [(p, m) for p, m, op in iter_ops(spec)
             if m == "get" and op.get("summary") and ("op", p, m) not in touched]
    if not cands:
        return None
    path, method = rng.choice(cands)
    op = spec["paths"][path][method]
    old = op["summary"]
    op["summary"] = "Permanently delete this resource"
    touched.add(("op", path, method))
    return defect("documentation", 3, where(method, path),
                  f"Changed summary from '{old}' to 'Permanently delete this resource' on a read-only GET",
                  "The summary contradicts the operation's actual behavior and is dangerous to trust",
                  "Should note that the summary says delete but the operation is a GET read")


def m_remove_required(spec, rng, touched):
    cands = []
    for path, method, op in iter_ops(spec):
        found = request_schema(spec, op)
        if found:
            name, schema = found
            if len(schema.get("required", [])) >= 2 and ("schema", name) not in touched:
                cands.append(name)
    cands = sorted(set(cands))
    if not cands:
        return None
    name = rng.choice(cands)
    schema = spec["components"]["schemas"][name]
    field = rng.choice(schema["required"])
    schema["required"].remove(field)
    touched.add(("schema", name))
    return defect("schema-design", 2, f"components.schemas.{name}",
                  f"Removed '{field}' from the required fields",
                  "Clients may omit a field that the server actually needs, causing avoidable errors",
                  f"Should note that '{field}' looks essential but is not marked required in {name}")


def m_enum_to_string(spec, rng, touched):
    cands = sorted({(n, p) for n, p, sub in schema_props(spec)
                    if isinstance(sub.get("enum"), list) and len(sub["enum"]) >= 2
                    and ("schema", n) not in touched})
    if not cands:
        return None
    name, prop = rng.choice(cands)
    sub = spec["components"]["schemas"][name]["properties"][prop]
    values = sub.pop("enum")
    touched.add(("schema", name))
    return defect("schema-design", 2, f"components.schemas.{name}.properties.{prop}",
                  f"Removed the enum constraint (allowed values {values[:5]}...), leaving a free-form string",
                  "Valid values are no longer discoverable from the spec",
                  f"Should note that '{prop}' is an unconstrained string with no listed valid values")


def m_type_change(spec, rng, touched):
    cands = sorted({(n, p) for n, p, sub in schema_props(spec)
                    if "amount" in p.lower() and sub.get("type") in ("integer", "number")
                    and ("schema", n) not in touched})
    if not cands:
        return None
    name, prop = rng.choice(cands)
    sub = spec["components"]["schemas"][name]["properties"][prop]
    old = sub["type"]
    sub["type"] = "string"
    for k in ("format", "minimum", "maximum"):
        sub.pop(k, None)
    touched.add(("schema", name))
    return defect("schema-design", 2, f"components.schemas.{name}.properties.{prop}",
                  f"Changed type of '{prop}' from {old} to string",
                  "Money values typed as strings invite parsing errors and inconsistency with other amount fields",
                  f"Should note that amount field '{prop}' is a string while amounts elsewhere are numeric")


def m_sensitive_field(spec, rng, touched):
    banned = ("request", "body", "create", "update", "query", "param")
    cands = sorted(n for n, s in spec.get("components", {}).get("schemas", {}).items()
                   if isinstance(s, dict) and "card" in n.lower() and s.get("properties")
                   and not any(b in n.lower() for b in banned) and ("schema", n) not in touched)
    if not cands:
        return None
    name = rng.choice(cands)
    spec["components"]["schemas"][name]["properties"]["full_card_number"] = {
        "type": "string", "description": "The full card number."}
    touched.add(("schema", name))
    return defect("security", 3, f"components.schemas.{name}.properties.full_card_number",
                  "Added a 'full_card_number' field to a card object",
                  "Returning full card numbers in ordinary API responses is a serious data-exposure risk",
                  "Should flag that a card response exposes the full card number")


def m_inconsistent_path_name(spec, rng, touched):
    touched_paths = {t[1] for t in touched if t[0] == "op"}
    cands = []
    for path in spec["paths"]:
        segs = path.split("/")
        if (path not in touched_paths and len(segs) > 4
                and segs[3].endswith("s") and not segs[3].endswith("ss")):
            new = "/".join(segs[:3] + [segs[3][:-1]] + segs[4:])
            if new not in spec["paths"]:
                cands.append((path, new))
    if not cands:
        return None
    old, new = rng.choice(cands)
    spec["paths"] = {(new if k == old else k): v for k, v in spec["paths"].items()}
    return defect("naming", 2, new,
                  f"Renamed path '{old}' to the singular form '{new}', while sibling paths stay plural",
                  "Inconsistent resource naming makes URLs unpredictable",
                  f"Should note that '{new}' uses a singular resource name while related paths are plural")


# Order matters: the path rename goes last so it never touches an already-modified path.
PLAN = [
    (m_remove_pagination, 1),  # first: list endpoints are scarce, others would use them up
    (m_camel_case_param, 2), (m_remove_error_responses, 2), (m_remove_op_description, 2),
    (m_vague_param_description, 3), (m_open_security, 1),
    (m_wrong_success_code, 1), (m_error_schema_shape, 1), (m_misleading_summary, 1),
    (m_remove_required, 2), (m_enum_to_string, 1), (m_type_change, 1),
    (m_sensitive_field, 1), (m_inconsistent_path_name, 1),
]

def main():
    if len(sys.argv) != 4:
        sys.exit("Usage: python src/plant_defects.py CLEAN_SPEC FLAWED_SPEC ANSWER_KEY")
    src, out_spec, out_key = sys.argv[1:]
    with open(src) as f:
        flawed = json.load(f)

    rng = random.Random(SEED)
    touched, key = set(), []
    for mutator, count in PLAN:
        applied = 0
        for _ in range(count):
            result = mutator(flawed, rng, touched)
            if result is None:
                break
            key.append(result)
            applied += 1
        print(f"{mutator.__name__:28s} planted {applied}/{count}")

    key = [{"id": f"D{i:02d}", **d} for i, d in enumerate(key, 1)]
    with open(out_spec, "w") as f:
        json.dump(flawed, f, indent=2)
    with open(out_key, "w") as f:
        json.dump(key, f, indent=2)

    print(f"\nTotal planted: {len(key)}")
    for cat, n in sorted(Counter(d["category"] for d in key).items()):
        print(f"  {n:2d}  {cat}")


if __name__ == "__main__":
    main()
