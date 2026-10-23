import json
import sys
from collections import Counter

path = sys.argv[1] if len(sys.argv) > 1 else "data/developer-api.json"
with open(path) as f:
    spec = json.load(f)

print("Title:     ", spec["info"].get("title"))
print("Version:   ", spec["info"].get("version"))
print("OpenAPI:   ", spec.get("openapi"))

paths = spec["paths"]
methods = {"get", "post", "put", "patch", "delete"}
tag_counts = Counter()
ops = 0
for p, item in paths.items():
    for method, op in item.items():
        if method in methods:
            ops += 1
            for t in op.get("tags", ["(none)"]):
                tag_counts[t] += 1

print("Paths:     ", len(paths))
print("Operations:", ops)
print("Schemas:   ", len(spec.get("components", {}).get("schemas", {})))
chars = len(json.dumps(spec))
print(f"Size:       {chars:,} chars (~{chars // 4:,} tokens)")

print("\nOperations per tag:")
for t, c in tag_counts.most_common():
    print(f"  {c:3d}  {t}")

print("\nPath groups (first 4 segments):")
groups = Counter("/".join(p.split("/")[:4]) for p in paths)
for g, c in sorted(groups.items()):
    print(f"  {c:3d}  {g}")
