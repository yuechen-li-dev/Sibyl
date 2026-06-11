#!/usr/bin/env python3
"""
Sibyl test runner entrypoint.

Usage:
    python sibyl_run.py                        # run all tests
    python sibyl_run.py <filter>               # run tests whose name contains <filter>
    python sibyl_run.py --bench                # run all benchmarks
    python sibyl_run.py --bench <filter>       # run matching benchmarks
    python sibyl_run.py --list                 # list all registered tests
    python sibyl_run.py --list-bench           # list all registered benchmarks

Internal (doom subprocess protocol):
    python sibyl_run.py --doom-case <name> --doom-artifact-dir <path>

Import your test modules before calling this, or pass them as a package:
    # Run specific test module
    python sibyl_run.py --modules my_tests another_tests [filter]
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

# Make sure sibyl package is importable when running from project root
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import sibyl
from sibyl.harness import (
    _DOOM_REGISTRY,
    _TEST_REGISTRY,
    _BENCHMARK_REGISTRY,
    _LOADED_MODULES,
    run_doom_case_in_child,
    set_sibyl_executable_path,
    run_all_tests,
    run_benchmarks,
    write_summary_json,
)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    # Set executable path for doom subprocess spawning
    set_sibyl_executable_path(Path(__file__))

    # ------------------------------------------------------------------
    # Module loading via --modules flag (must happen before doom dispatch
    # so doom cases registered in those modules are available in the child)
    # ------------------------------------------------------------------
    module_names: list[str] = []
    remaining: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--modules":
            i += 1
            while i < len(args) and not args[i].startswith("--"):
                module_names.append(args[i])
                i += 1
        elif args[i] == "--":
            remaining.extend(args[i + 1:])
            break
        else:
            remaining.append(args[i])
            i += 1
    args = remaining

    for mod_name in module_names:
        try:
            importlib.import_module(mod_name)
            _LOADED_MODULES.append(mod_name)
        except ImportError as exc:
            print(f"[sibyl] failed to import module '{mod_name}': {exc}", file=sys.stderr)
            return 2

    # ------------------------------------------------------------------
    # Doom child mode — handled after modules are loaded
    # ------------------------------------------------------------------
    if len(args) >= 4 and args[0] == "--doom-case" and args[2] == "--doom-artifact-dir":
        case_name = args[1]
        artifact_dir = Path(args[3])
        return run_doom_case_in_child(case_name, artifact_dir)

    # ------------------------------------------------------------------
    # --list / --list-bench
    # ------------------------------------------------------------------
    if args and args[0] == "--list":
        print("Registered tests:")
        for tc in sorted(_TEST_REGISTRY, key=lambda t: t.name):
            xfail_tag = " [xfail]" if tc.xfail else ""
            print(f"  {tc.name}{xfail_tag}")
        return 0

    if args and args[0] == "--list-bench":
        print("Registered benchmarks:")
        for bc in sorted(_BENCHMARK_REGISTRY, key=lambda b: b.name):
            print(f"  {bc.name}  iterations={bc.iterations}")
        return 0

    # ------------------------------------------------------------------
    # Bench mode
    # ------------------------------------------------------------------
    if args and args[0] == "--bench":
        filter_str = args[1] if len(args) > 1 else ""
        return run_benchmarks(filter_str)

    # ------------------------------------------------------------------
    # Normal test run
    # ------------------------------------------------------------------
    filter_str = args[0] if args else ""
    exit_code, summary_records = run_all_tests(filter_str)

    summary_path = write_summary_json(summary_records)
    print(f"summary: {summary_path}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
