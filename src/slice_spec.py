import json
import sys


def collect_refs(node, found):
    """Walk any JSON structure and gather every '#/...' $ref string."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/"):
                found.add(value)
            else:
                collect_refs(value, found)
    elif isinstance(node, list):
        for item in node:
            collect_refs(item, found)


def resolve(spec, ref):
    """Look up a ref like '#/components/schemas/Card' inside the spec."""
    node = spec
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def main():
    if len(sys.argv) < 4:
        sys.exit("Usage: python src/slice_spec.py INPUT OUTPUT PREFIX [PREFIX ...]")
    src, out, *prefixes = sys.argv[1:]

    with open(src) as f:
        spec = json.load(f)

    kept_paths = {
        p: item
        for p, item in spec["paths"].items()
        if any(p.startswith(prefix) for prefix in prefixes)
    }
    if not kept_paths:
        sys.exit(f"No paths matched {prefixes}. Check the prefixes against inspect_spec output.")

    # Follow refs transitively: schemas can reference other schemas.
    pending, seen = set(), set()
    collect_refs(kept_paths, pending)
    while pending:
        ref = pending.pop()
        if ref in seen:
            continue
        seen.add(ref)
        try:
            collect_refs(resolve(spec, ref), pending)
        except KeyError:
            print(f"WARNING: could not resolve {ref} in the original spec")

    # Rebuild components containing only what is needed.
    components = {}
    for ref in seen:
        parts = ref[2:].split("/")  # e.g. ['components', 'schemas', 'Card']
        if parts[0] != "components" or len(parts) < 3:
            continue
        section, name = parts[1], parts[2]
        components.setdefault(section, {})[name] = spec["components"][section][name]

    # Always keep auth definitions: security is something personas should review.
    if "securitySchemes" in spec.get("components", {}):
        components["securitySchemes"] = spec["components"]["securitySchemes"]

    used_tags = {t for item in kept_paths.values() for op in item.values()
                 if isinstance(op, dict) for t in op.get("tags", [])}
    sliced = {
        "openapi": spec.get("openapi"),
        "info": spec.get("info"),
        "servers": spec.get("servers"),
        "security": spec.get("security"),
        "tags": [t for t in spec.get("tags", []) if t.get("name") in used_tags],
        "paths": kept_paths,
        "components": components,
    }
    sliced = {k: v for k, v in sliced.items() if v not in (None, [], {})}

    # Safety check: every ref in the output must resolve inside the output.
    check = set()
    collect_refs(sliced, check)
    broken = []
    for ref in check:
        try:
            resolve(sliced, ref)
        except KeyError:
            broken.append(ref)

    with open(out, "w") as f:
        json.dump(sliced, f, indent=2)

    chars = len(json.dumps(sliced))
    ops = sum(1 for item in kept_paths.values() for m in item
              if m in {"get", "post", "put", "patch", "delete"})
    print(f"Kept {len(kept_paths)} paths, {ops} operations")
    print(f"Kept {sum(len(v) for v in components.values())} component definitions")
    print(f"Size: {chars:,} chars (~{chars // 4:,} tokens)")
    print("Broken refs:", broken if broken else "none")


if __name__ == "__main__":
    main()
