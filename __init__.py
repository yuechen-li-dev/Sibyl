"""
Sibyl — Python test harness.
"""

from sibyl.harness import (
    # Decorators
    fact,
    theory,
    benchmark,
    doom_fact,

    # Context types
    TestContext,
    BenchmarkContext,

    # Assertion helpers
    assert_true,
    assert_false,
    assert_equal,
    assert_not_equal,
    assert_near,
    assert_contains,
    assert_not_contains,
    assert_sequence_equal,
    fail,
    skip,

    # Doom helpers
    foretell_doom,
    assert_doom,
    set_sibyl_executable_path,

    # Runner
    run_all_tests,
    run_benchmarks,
    execute_benchmarks,
    write_summary_json,

    # Result types (for type annotations in test files)
    Failure,
    Skip,
    BenchmarkResult,
    DoomRunResult,

    # Internal (needed by sibyl_run.py)
    _LOADED_MODULES,
)

__all__ = [
    "fact", "theory", "benchmark", "doom_fact",
    "TestContext", "BenchmarkContext",
    "assert_true", "assert_false", "assert_equal", "assert_not_equal",
    "assert_near", "assert_contains", "assert_not_contains",
    "assert_sequence_equal", "fail", "skip",
    "foretell_doom", "assert_doom", "set_sibyl_executable_path",
    "run_all_tests", "run_benchmarks", "execute_benchmarks", "write_summary_json",
    "Failure", "Skip", "BenchmarkResult", "DoomRunResult",
]
