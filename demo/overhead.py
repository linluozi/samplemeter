"""Cost of auditing: same real corpus, plain vs traced (single process)."""

import importlib.metadata as md
import time

import real_corpus_lib
from samplemeter import Auditor

lines = []
for dist in md.distributions():
    lines.extend(dist.requires or [])


def run():
    for line in lines:
        real_corpus_lib.parse_requirement(line)


run()  # warm up imports and caches once for both variants
start = time.perf_counter()
run()
plain = time.perf_counter() - start

auditor = Auditor(scope="all")
real_corpus_lib.parse_requirement = auditor.wrap(real_corpus_lib.parse_requirement)
run()
start = time.perf_counter()
run()
traced = time.perf_counter() - start

print(f"requirements: {len(lines)}")
print(f"plain   : {plain * 1000:.1f} ms")
print(f"traced  : {traced * 1000:.1f} ms")
print(f"overhead: {traced / plain:.1f}x")
