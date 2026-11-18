"""Core instrumentation: fingerprint call arguments and record execution paths.

The point of this module is narrow: a differential test that claims N random
samples is only as good as the number of *distinct* argument tuples that
actually reached the function under test.  Counting loop iterations is easy and
lying; counting distinct inputs and distinct executed-line signatures is not.

Everything here is single-threaded and CPython-only by design (``sys.settrace``).
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import random
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

_SELF_DIR = __file__.rsplit("/", 1)[0]


def fingerprint(obj: Any, _depth: int = 0) -> str:
    """Deterministic, type-tagged representation used for de-duplication.

    Type tags are deliberate: ``1``, ``1.0``, ``True`` and ``"1"`` are four
    different inputs.  Containers are canonicalised (dict keys and set members
    are sorted), so insertion order does not create fake diversity.
    """
    if _depth > 12:
        return "...depth-limit"
    t = type(obj)
    if obj is None:
        return "none"
    if t is bool:
        return "bool:1" if obj else "bool:0"
    if t is int:
        return f"int:{obj}"
    if t is float:
        if obj != obj:
            return "float:nan"
        return f"float:{obj!r}"
    if t is str:
        return f"str:{obj}"
    if t is bytes:
        return f"bytes:{obj.hex()}"
    if t is list or t is tuple:
        inner = ",".join(fingerprint(x, _depth + 1) for x in obj)
        return f"{t.__name__}[{inner}]"
    if t is dict:
        pairs = sorted(
            (fingerprint(k, _depth + 1), fingerprint(v, _depth + 1))
            for k, v in obj.items()
        )
        return "dict{" + ",".join(f"{k}={v}" for k, v in pairs) + "}"
    if t is set or t is frozenset:
        members = sorted(fingerprint(x, _depth + 1) for x in obj)
        return f"{t.__name__}{{{','.join(members)}}}"
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        inner = ",".join(
            f"{f.name}={fingerprint(getattr(obj, f.name), _depth + 1)}"
            for f in dataclasses.fields(obj)
        )
        return f"dataclass:{t.__module__}.{t.__qualname__}{{{inner}}}"
    state = getattr(obj, "__dict__", None)
    if isinstance(state, dict) and state:
        return (
            f"obj:{t.__module__}.{t.__qualname__}#"
            + fingerprint(state, _depth + 1)
        )
    text = repr(obj)
    if " at 0x" in text:
        # Address-dependent repr: cannot be de-duplicated, flag it instead of
        # pretending the sample is distinct.
        return f"opaque:{t.__module__}.{t.__qualname__}"
    return f"repr:{t.__module__}.{t.__qualname__}:{text}"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]


@dataclass(frozen=True)
class CallRecord:
    input_fp: str
    path_fp: str
    output_fp: str
    lines: tuple[tuple[str, int], ...]
    raised: bool


@dataclass
class SeedReset:
    filename: str
    lineno: int
    function: str
    calls: int


@dataclass
class Report:
    nominal: int
    unique_inputs: int
    unique_paths: int
    unique_pairs: int
    unique_outputs: int
    opaque_inputs: int
    input_counts: dict[str, int] = field(default_factory=dict)
    input_previews: dict[str, str] = field(default_factory=dict)
    path_counts: dict[str, int] = field(default_factory=dict)
    seed_resets: list[SeedReset] = field(default_factory=list)
    static_seed_findings: list[str] = field(default_factory=list)
    raised: int = 0

    def top_inputs(self, limit: int = 3) -> list[tuple[int, str]]:
        ranked = sorted(self.input_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [(count, self.input_previews.get(fp, fp)) for fp, count in ranked[:limit]]

    @property
    def inflation(self) -> float:
        return self.nominal / self.unique_inputs if self.unique_inputs else float("inf")

    @property
    def path_collapse(self) -> bool:
        return self.nominal > 1 and self.unique_paths <= 1

    def repeated_seed_sites(self, threshold: int = 2) -> list[SeedReset]:
        return [s for s in self.seed_resets if s.calls > threshold]

    def is_degenerate(
        self,
        min_unique: int | None = None,
        max_inflation: float = 2.0,
    ) -> bool:
        if min_unique is not None and self.unique_inputs < min_unique:
            return True
        if self.repeated_seed_sites():
            return True
        if self.nominal >= 10 and self.inflation >= max_inflation and self.path_collapse:
            return True
        return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "nominal": self.nominal,
            "unique_inputs": self.unique_inputs,
            "unique_paths": self.unique_paths,
            "unique_pairs": self.unique_pairs,
            "unique_outputs": self.unique_outputs,
            "inflation": round(self.inflation, 4),
            "opaque_inputs": self.opaque_inputs,
            "top_inputs": [
                {"calls": count, "preview": preview}
                for count, preview in self.top_inputs(3)
            ],
            "raised_calls": self.raised,
            "path_collapse": self.path_collapse,
            "repeated_seed_sites": [
                {
                    "file": s.filename,
                    "line": s.lineno,
                    "function": s.function,
                    "calls": s.calls,
                }
                for s in self.repeated_seed_sites()
            ],
            "static_seed_findings": self.static_seed_findings,
        }


class Auditor:
    """Wrap one function and count what actually reaches it."""

    def __init__(self, scope: str = "target", hash_lines: bool = True) -> None:
        if scope not in ("all", "target"):
            raise ValueError("scope must be 'all' or 'target'")
        # "target": only frames from the function's own file (stable branches).
        # "all": every Python frame, including library internals and one-time
        #        lazy-import frames from the first call.
        self.scope = scope
        self.hash_lines = hash_lines
        self._records: list[CallRecord] = []
        self._stack: list[list[tuple[str, int]]] = []
        self._previews: dict[str, str] = {}
        self._seed_calls: dict[tuple[str, int, str], int] = {}
        self._target_file: str | None = None
        self._orig_seed: Callable[..., None] | None = None
        self.static_seed_findings: list[str] = []

    # -- tracing -----------------------------------------------------------
    def _tracer(self, frame, event, arg):
        if frame.f_code.co_filename.startswith(_SELF_DIR):
            return None
        if event != "call":
            return None
        if self.scope == "target" and frame.f_code.co_filename != self._target_file:
            return None
        frame.f_trace_lines = True
        return self._line_tracer

    def _line_tracer(self, frame, event, arg):
        if event == "line" and self._stack:
            # A recursive (re-entrant) call pushes its own frame, so lines must
            # go to the innermost active call, not to a single shared list.
            self._stack[-1].append((frame.f_code.co_filename, frame.f_lineno))
        return self._line_tracer

    # -- seeding -----------------------------------------------------------
    def _install_seed_probe(self) -> None:
        self._orig_seed = random.seed

        def seed_probe(a=None, version=2):
            frame = sys._getframe(1)
            key = (frame.f_code.co_filename, frame.f_lineno, frame.f_code.co_name)
            self._seed_calls[key] = self._seed_calls.get(key, 0) + 1
            return self._orig_seed(a, version)

        random.seed = seed_probe  # type: ignore[assignment]

    def _remove_seed_probe(self) -> None:
        if self._orig_seed is not None:
            random.seed = self._orig_seed  # type: ignore[assignment]
            self._orig_seed = None

    # -- wrapping ----------------------------------------------------------
    def wrap(self, func: Callable[..., Any], skip_first: bool | None = None) -> Callable[..., Any]:
        target = getattr(func, "__func__", func)  # unwrap bound methods
        code = target.__code__
        self._target_file = code.co_filename
        qualname = getattr(target, "__qualname__", "")
        if skip_first is None:
            # Only drop the first argument for real methods: a nested function
            # (``<locals>``) also has a dotted qualname but no self/cls.
            first = code.co_varnames[0] if code.co_varnames else ""
            skip_first = first in ("self", "cls") and "<locals>" not in qualname

        def wrapper(*args, **kwargs):
            fp_args = args[1:] if skip_first else args
            canonical = fingerprint((fp_args, kwargs))
            input_fp = _digest(canonical)
            if input_fp not in self._previews:
                self._previews[input_fp] = canonical[:200]
            lines: list[tuple[str, int]] = []
            output_fp = ""
            self._stack.append(lines)
            previous = sys.gettrace()
            raised = False
            sys.settrace(self._tracer)
            try:
                result = func(*args, **kwargs)
                output_fp = _digest(fingerprint(result))
                return result
            except BaseException as exc:
                raised = True
                output_fp = f"exc:{type(exc).__name__}"
                raise
            finally:
                sys.settrace(previous)
                self._stack.pop()
                # Collapse consecutive duplicates, then hash the ordered lines.
                compact: list[tuple[str, int]] = []
                for item in lines:
                    if not compact or compact[-1] != item:
                        compact.append(item)
                path_fp = _digest(repr(compact)) if self.hash_lines else ""
                self._records.append(
                    CallRecord(
                        input_fp=input_fp,
                        path_fp=path_fp,
                        output_fp=output_fp,
                        lines=tuple(compact),
                        raised=raised,
                    )
                )

        wrapper.__name__ = getattr(target, "__name__", "wrapped")
        wrapper.__qualname__ = getattr(target, "__qualname__", wrapper.__name__)
        wrapper.__doc__ = getattr(func, "__doc__", None)
        wrapper.__wrapped__ = func
        return wrapper

    # -- results -----------------------------------------------------------
    def report(self) -> Report:
        input_counts: dict[str, int] = {}
        path_counts: dict[str, int] = {}
        pairs: set[tuple[str, str]] = set()
        outputs: set[str] = set()
        for rec in self._records:
            outputs.add(rec.output_fp)
            input_counts[rec.input_fp] = input_counts.get(rec.input_fp, 0) + 1
            path_counts[rec.path_fp] = path_counts.get(rec.path_fp, 0) + 1
            pairs.add((rec.input_fp, rec.path_fp))
        opaque = sum(1 for fp in input_counts if fp.startswith("opaque:"))
        seeds = [
            SeedReset(filename=f, lineno=ln, function=fn, calls=c)
            for (f, ln, fn), c in sorted(self._seed_calls.items())
        ]
        return Report(
            nominal=len(self._records),
            unique_inputs=len(input_counts),
            unique_paths=len(path_counts),
            unique_pairs=len(pairs),
            unique_outputs=len(outputs),
            opaque_inputs=opaque,
            input_counts=input_counts,
            input_previews=dict(self._previews),
            path_counts=path_counts,
            seed_resets=seeds,
            static_seed_findings=list(self.static_seed_findings),
            raised=sum(1 for r in self._records if r.raised),
        )

    def reset(self) -> None:
        self._records.clear()
        self._seed_calls.clear()
        self._previews.clear()


def scan_seed_resets(path: str) -> list[str]:
    """Static check for seeding inside a loop body.

    This catches the obvious shape (``random.seed(...)`` written inside a
    ``for``/``while``).  The subtler shape -- a helper that seeds internally,
    called once per loop iteration -- can only be caught at runtime, which is
    what :class:`Auditor`'s seed probe does.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError) as exc:
        return [f"{path}: cannot parse ({exc.__class__.__name__})"]

    findings: list[str] = []

    def is_seed_call(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        target = node.func
        if isinstance(target, ast.Attribute) and target.attr == "seed":
            return True
        if isinstance(target, ast.Name) and target.id in ("Random", "random"):
            return "Random" in target.id
        return False

    for loop in ast.walk(tree):
        if not isinstance(loop, (ast.For, ast.AsyncFor, ast.While)):
            continue
        for stmt in loop.body:
            for node in ast.walk(stmt):
                if is_seed_call(node):
                    findings.append(
                        f"{path}:{node.lineno}: seeding inside a loop body "
                        f"({loop.__class__.__name__})"
                    )
    return findings
