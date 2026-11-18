"""Claims 2,000 samples; produces one distinct document."""

import os

import inflated_lib

N = int(os.environ.get("SM_N", "2000"))
rows = []
for _ in range(N):
    rows.append(inflated_lib.resolve(inflated_lib.one_doc()))

print(f"harness iterations: {N}, distinct rows: {len(set(map(tuple, rows)))}")
