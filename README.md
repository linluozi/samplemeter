# samplemeter

[![tests](https://github.com/linluozi/samplemeter/actions/workflows/tests.yml/badge.svg)](https://github.com/linluozi/samplemeter/actions/workflows/tests.yml)

A harness that loops 2,000 times reports 2,000 samples. `samplemeter` measures how many
**distinct inputs** actually reached the function under test, how many **distinct execution
paths** those calls took, and whether the generator re-seeded itself on every iteration.
It is a measuring instrument for differential tests, not a profiler and not a benchmark.

Upstream: none

## Why

Differential and randomized tests are the evidence behind most "N samples, 0 mismatches"
claims, and the loop counter is the easiest number in the world to lie with. A generator
that creates its RNG *inside* the sampling function returns the same document every time;
the harness then runs 2,000 iterations over one input and reports 2,000 samples. This exact
defect is recorded in this project's own 2026-11-01 log: 2,000 claimed random documents
collapsed to a single 557-character document, an effective coverage of 10 argument
combinations — a 200x inflation.

Existing tools do not answer this question: `coverage.py` reports which lines ran at least
once across the whole session, `hypothesis` counts examples only for its own strategies, and
a profiler reports time, not input identity. `samplemeter` fingerprints each call's arguments
(canonicalised, type-tagged) and each call's executed-line signature, then reports nominal vs. unique inputs vs.
paths vs. outputs.

## Quick start (no install)

Requires CPython 3.10+ (developed and measured on 3.12.3); the real-corpus demo also needs
`packaging` (`pip install -e ".[demo]"`, or any environment that already ships it).

First, the corrected harness -- 2,000 iterations, 2,000 distinct documents, exit code 0:

```bash
PYTHONPATH=src python3 -m samplemeter run demo/honest_harness.py \
    --target demo/honest_lib.py:resolve --min-unique 1000
```

```
harness iterations: 2000, distinct rows: 2000
samplemeter report
  nominal calls       : 2000
  unique inputs       : 2000  (sha256 of canonical args)
  unique paths        : 14  (executed-line signatures)
  unique (input,path) : 2000
  unique outputs      : 2000  (sha256 of returned value)
  inflation           : 1.00x
  opaque inputs       : 0
  calls that raised   : 0
  verdict             : OK
```

Now the harness this tool exists for -- same loop counter, one document, exit code 1:

```bash
PYTHONPATH=src python3 -m samplemeter run demo/inflated_harness.py \
    --target demo/inflated_lib.py:resolve
```

```
harness iterations: 2000, distinct rows: 1
samplemeter report
  nominal calls       : 2000
  unique inputs       : 1  (sha256 of canonical args)
  unique paths        : 1  (executed-line signatures)
  unique (input,path) : 1
  unique outputs      : 1  (sha256 of returned value)
  inflation           : 2000.00x
  opaque inputs       : 0
  top input           : 2000 call(s) tuple[tuple[dict{str:base=str:https://example.com/a/,str:items=list[dict{str:link=str:../post/0},dict{str:link=str:../post/1}]}],dict{}]
  calls that raised   : 0
  seed resets         :
    demo/inflated_lib.py:8 in one_doc() x2000
  verdict             : DEGENERATE
```

Both reports are verbatim output from 2026-11-18, CPython 3.12.3. The non-zero exit code on
DEGENERATE is deliberate, so CI can refuse the claim.

## What it reports

| field | meaning |
| --- | --- |
| `nominal` | calls that reached the wrapped function |
| `unique inputs` | sha256 of the canonical, type-tagged argument tuple (positional + keyword) |
| `unique paths` | sha256 of the ordered sequence of executed `(file, line)` pairs per call |
| `unique (input, path)` | distinct pairs; catches "many docs, one branch" |
| `unique outputs` | sha256 of each call's return value (or exception type); catches "many inputs, one answer" |
| `inflation` | `nominal / unique inputs` |
| `opaque inputs` | arguments whose address-dependent `repr` cannot be de-duplicated (reported, never counted as unique) |
| `top input` | printed only when duplicates exist: how many calls shared the most frequent input, plus a 200-char preview of it |
| `seed resets` | runtime probe: same `random.seed(...)` call site executed more than once |
| `static findings` | AST scan: `random.seed(...)` / `Random(...)` written inside a `for`/`while` body |

`--min-unique N` fails when distinct inputs fall below `N`; `--min-paths N` fails when the
number of distinct execution paths falls below `N` (the "500 documents, one happy path" case);
`--max-inflation X` (default 2.0) fails when inflation is at least `X` **and** every call took
the same path. `--json` emits the
same data as machine-readable JSON (harness stdout is redirected to stderr to keep the JSON clean).

### CLI

```bash
samplemeter run HARNESS.py --target path/to/module.py:function \
    [--min-unique N] [--min-paths N] [--max-inflation X] [--scope target|all] [--json]
```

`--scope target` (default) hashes only lines from the target function's own file, which keeps
signatures stable. `--scope all` also hashes library internals and is what the real-corpus
demo uses; note that the **first** call in a process can carry one-time lazy-import frames, so
one extra path signature may appear (visible below: 43 paths for 392 calls).

### Library

```python
from samplemeter import Auditor

auditor = Auditor(scope="target")
parse = auditor.wrap(parse_document)      # methods: self/cls is skipped automatically
for doc in documents:
    parse(doc)
report = auditor.report()
print(report.nominal, report.unique_inputs, report.unique_paths, report.inflation)
```

## Evidence from real runs

All numbers below were produced on 2026-11-18 on this machine (AMD EPYC 7K62, 4 cores,
CPython 3.12.3) and can be reproduced with the listed commands from the repository root.

**1. The reconstructed inflation shape** (`PYTHONPATH=src python3 -m samplemeter run demo/inflated_harness.py --target demo/inflated_lib.py:resolve`)

```
nominal 2000 | unique inputs 1 | paths 1 | inflation 2000.00x | seed resets 2000 | DEGENERATE
```

**2. The corrected harness** (`... demo/honest_harness.py --target demo/honest_lib.py:resolve --min-unique 1000`)

```
nominal 2000 | unique inputs 2000 | paths 14 | inflation 1.00x | OK
```

**3. A real corpus, not a synthetic one**: every `Requires-Dist` line of every distribution
installed on the machine (via `importlib.metadata`), parsed with `packaging.requirements`:

```
PYTHONPATH=src python3 -m samplemeter run demo/real_corpus_harness.py \
    --target demo/real_corpus_lib.py:parse_requirement --scope all --min-unique 50
```

```
real Requires-Dist lines: 392 (parsed 392, errors 0)
nominal 392 | unique inputs 369 | paths 43 | unique (input,path) 369 | inflation 1.06x | OK
```

23 of the 392 lines are exact duplicates (the same requirement appearing in several
distributions' metadata), which is why the honest inflation is 1.06x rather than 1.00x.
An independent oracle for that number needs no library code:

```bash
python3 -c "import importlib.metadata as md; L=[r for d in md.distributions() for r in (d.requires or [])]; print(len(L), len(set(L)))"
# 392 369 -- matches the tool's nominal / unique inputs exactly
```

**4. Diversity without branch coverage**: 500 distinct documents that all pass the same
validation branch:

```
PYTHONPATH=src python3 -m samplemeter run demo/collapsed_harness.py \
    --target demo/collapsed_lib.py:validate --min-paths 5
```

```
documents: 500, accepted: 500
nominal 500 | unique inputs 500 | paths 1 | unique (input,path) 500 | outputs 1 | inflation 1.00x | DEGENERATE
reason: unique paths 1 < --min-paths 5
```

Inflation alone would pass this harness; the path signature is what exposes it. This is why
the report keeps all four numbers instead of a single score.

**5. The cost of measuring** (`PYTHONPATH=src python3 demo/overhead.py`): 392 real
requirement lines take **24.7 ms** plain and **374.6 ms** traced — a **15.2x** slowdown,
because `sys.settrace` emits a line event per executed line. This is an audit pass you run
once to validate a harness, not something to leave enabled in a timing loop.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

`28 tests, 0 failures, 0.51 s` (measured on CPython 3.12.3). The same suite also passes on
3.10 and 3.11 in CI (run
[44221727460](https://github.com/linluozi/samplemeter/actions/runs/44221727460), all three
matrix jobs green, 2026-11-18). They cover: type-tagged fingerprints (int vs float
vs bool vs str, NaN, -0.0, non-ASCII, bytes), dict/set order invariance, deep-nesting bounds,
opaque-object marking, unique-vs-nominal counting, kwargs as part of the input, path
distinction across branches, wrapper transparency, exception propagation and counting,
`self` skipping for methods, runtime seed-reset detection and probe restoration on exception,
AST loop detection and syntax-error handling, plus output-diversity counting, nested-function method detection, a recursion regression (verified to fail against a shared-buffer variant), a top-input ranking check and four CLI end-to-end runs (inflated → exit 1, honest → exit 0,
collapsed with `--min-paths` → exit 1, real corpus → exit 0).

## Limits

- Single-threaded only: `sys.settrace` is process-global, so concurrent auditing is undefined.
- Re-entrant (recursive) calls are supported: every active call keeps its own line buffer.
- The wrapper must replace the function object the harness resolves (the CLI does this on the
  target module before running the harness, so `from mod import func` is fine).
- Inputs are compared by canonical value, not by semantic equivalence: two documents that
  differ in a field the function ignores count as two inputs (path signatures are the guard
  against that).
- `random.Random(seed)` construction cannot be intercepted; the AST scan catches it only when
  written inside a loop body. The inflation/path check catches the rest.

## 本轮创新

第一个把「样本有效性」当作可测量对象的工具：对每次调用做**参数级去重**（规范哈希）+ **逐调用执行行签名** +
**运行期 seed 重置探针**，直接给出 nominal / unique / paths / inflation 四个数，并在 2000 次迭代
只产出 1 个输入的复现用例上判 DEGENERATE（2000.00x）。与 `coverage.py`（整场会话的行覆盖，无法看到
「2000 次调用共享同一输入」）、`hypothesis`（只统计自家策略的 examples）、cProfile（只报时间）不同：
它衡量的是差分测试的**证据强度**，且结论可被一条命令推翻（`--min-unique` / 退出码）。

## License

MIT, Copyright (c) 2026 linluozi. This is an original project (`Upstream: none`).
