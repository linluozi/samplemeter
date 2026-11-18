"""Command line entry point: run a harness with one function instrumented."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.util
import json
import os
import runpy
import sys

from .core import Auditor, Report, scan_seed_resets


def _load_target(spec: str):
    if ":" not in spec:
        raise SystemExit(f"--target must be path/to/file.py:function, got {spec!r}")
    path, attr_path = spec.split(":", 1)
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise SystemExit(f"target file not found: {path}")
    directory = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    if directory not in sys.path:
        sys.path.insert(0, directory)
    module = importlib.import_module(stem)
    obj = module
    for part in attr_path.split("."):
        obj = getattr(obj, part)
    return module, attr_path, obj


def _short(path: str) -> str:
    try:
        rel = os.path.relpath(path)
    except ValueError:
        return path
    return path if rel.startswith("..") else rel


def _print_report(report: Report, *, degenerate: bool) -> None:
    print("samplemeter report")
    print(f"  nominal calls       : {report.nominal}")
    print(f"  unique inputs       : {report.unique_inputs}  (sha256 of canonical args)")
    print(f"  unique paths        : {report.unique_paths}  (executed-line signatures)")
    print(f"  unique (input,path) : {report.unique_pairs}")
    print(f"  inflation           : {report.inflation:.2f}x")
    print(f"  opaque inputs       : {report.opaque_inputs}")
    print(f"  calls that raised   : {report.raised}")
    if report.repeated_seed_sites():
        print("  seed resets         :")
        for site in report.repeated_seed_sites():
            print(f"    {_short(site.filename)}:{site.lineno} in {site.function}() x{site.calls}")
    for finding in report.static_seed_findings:
        print(f"  static              : {_short(finding)}")
    print(f"  verdict             : {'DEGENERATE' if degenerate else 'OK'}")


def _verdict(
    report: Report,
    min_unique: int | None,
    min_paths: int | None,
    max_inflation: float,
) -> bool:
    reasons = []
    if min_unique is not None and report.unique_inputs < min_unique:
        reasons.append(f"unique inputs {report.unique_inputs} < --min-unique {min_unique}")
    if min_paths is not None and report.unique_paths < min_paths:
        reasons.append(f"unique paths {report.unique_paths} < --min-paths {min_paths}")
    if report.repeated_seed_sites():
        reasons.append("the same seed site ran more than once")
    if (
        report.nominal >= 10
        and report.inflation >= max_inflation
        and report.path_collapse
    ):
        reasons.append(
            f"inflation {report.inflation:.2f}x with a single executed path"
        )
    if reasons:
        for reason in reasons:
            print(f"  reason              : {reason}", file=sys.stderr)
    return bool(reasons)


def cmd_run(args: argparse.Namespace) -> int:
    module, attr_path, func = _load_target(args.target)
    auditor = Auditor(scope=args.scope)
    setattr(module, attr_path, auditor.wrap(func))

    findings = scan_seed_resets(args.harness)
    target_file = sys.modules[module.__name__].__file__
    if target_file:
        findings += scan_seed_resets(target_file)
    auditor.static_seed_findings = findings

    auditor._install_seed_probe()
    try:
        if args.json:
            with contextlib.redirect_stdout(sys.stderr):
                runpy.run_path(args.harness, run_name="__main__")
        else:
            runpy.run_path(args.harness, run_name="__main__")
    finally:
        auditor._remove_seed_probe()

    report = auditor.report()
    degenerate = _verdict(report, args.min_unique, args.min_paths, args.max_inflation)
    if args.json:
        payload = report.as_dict()
        payload["degenerate"] = degenerate
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_report(report, degenerate=degenerate)
    return 1 if degenerate else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="samplemeter",
        description="Count distinct inputs and execution paths behind a harness's nominal sample count.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a harness with one function instrumented")
    run.add_argument("harness", help="path to the harness script to execute")
    run.add_argument(
        "--target",
        required=True,
        help="path/to/module.py:function (dotted attributes allowed)",
    )
    run.add_argument("--min-unique", type=int, default=None)
    run.add_argument("--min-paths", type=int, default=None)
    run.add_argument("--max-inflation", type=float, default=2.0)
    run.add_argument("--scope", choices=["all", "target"], default="target")
    run.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    return 2
