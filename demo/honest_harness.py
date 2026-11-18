"""2,000 iterations, 2,000 distinct documents: the same harness, fixed."""

import os

import honest_lib

N = int(os.environ.get("SM_N", "2000"))
rng = honest_lib.make_rng()
rows = []
for _ in range(N):
    rows.append(honest_lib.resolve(honest_lib.one_doc(rng)))

print(f"harness iterations: {N}, distinct rows: {len(set(map(tuple, rows)))}")
