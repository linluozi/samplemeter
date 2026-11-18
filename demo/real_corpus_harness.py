"""Audit the parser on every Requires-Dist line installed on this machine."""

import importlib.metadata as md

import real_corpus_lib

lines = []
for dist in md.distributions():
    for requirement in dist.requires or []:
        lines.append(requirement)

ok = errors = 0
for line in lines:
    try:
        real_corpus_lib.parse_requirement(line)
        ok += 1
    except Exception:
        errors += 1

print(f"real Requires-Dist lines: {len(lines)} (parsed {ok}, errors {errors})")
