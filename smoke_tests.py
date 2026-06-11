"""
Sibyl smoke tests.

Verifies that all harness primitives behave correctly.
Run with: python sibyl_run.py --modules smoke_tests
"""

import sys
from pathlib import Path

# Ensure sibyl is importable when run standalone
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sibyl import (
    fact, theory, benchmark, doom_fact,
    TestContext, BenchmarkContext,
    assert_true, assert_false, assert_equal, assert_not_equal,
    assert_near, assert_contains, assert_not_contains,
    assert_sequence_equal, fail, skip,
    foretell_doom, assert_doom,
    execute_benchmarks,
)
from sibyl.harness import (
    _TEST_REGISTRY, _BENCHMARK_REGISTRY, _DOOM_REGISTRY,
    Failure,
)


# ---------------------------------------------------------------------------
# Basic assertions
# ---------------------------------------------------------------------------

@fact
def smoke_fact_passes(ctx: TestContext):
    assert_true(ctx, True, "basic true assertion should pass")


@fact
def smoke_fact_supports_rich_assertions(ctx: TestContext):
    assert_true(ctx, True, "true assertion")
    assert_false(ctx, False, "false assertion")
    assert_equal(ctx, 3, 3, "equal integers")
    assert_not_equal(ctx, "host", "candidate", "distinct strings")


@fact
def smoke_fact_accumulates_multiple_failures(ctx: TestContext):
    """All three assertions should fire, not just the first."""
    inner = TestContext("accumulation_check")
    assert_equal(inner, 1, 2, "first mismatch")
    assert_equal(inner, "a", "b", "second mismatch")
    assert_true(inner, False, "third mismatch")
    assert_equal(ctx, 3, len(inner.failures), "all three failures should be recorded")


@fact
def smoke_skip_works(ctx: TestContext):
    skip(ctx, "example skip stays visible without failing")
    # Nothing below here should execute
    assert_true(ctx, False, "should not reach this")


@fact
def smoke_fact_supports_sequence_equal(ctx: TestContext):
    assert_sequence_equal(ctx, [1, 2, 3, 5, 8], [1, 2, 3, 5, 8], "fibonacci prefix")


@fact
def smoke_sequence_length_mismatch_is_caught(ctx: TestContext):
    inner = TestContext("seq_len_check")
    assert_sequence_equal(inner, [1, 2, 3], [1, 2], "length mismatch")
    assert_true(ctx, inner.has_failures, "length mismatch should be a failure")
    assert_contains(ctx, "length mismatch", inner.failures[0].message, "message should mention mismatch")


@fact
def smoke_assert_near_passes_within_tolerance(ctx: TestContext):
    assert_near(ctx, 10.0, 10.2, 0.3, "within tolerance")


@fact
def smoke_assert_near_fails_outside_tolerance(ctx: TestContext):
    inner = TestContext("near_fail_check")
    assert_near(inner, 1.0, 1.4, 0.2, "outside tolerance")
    assert_true(ctx, inner.has_failures, "near failure should be recorded")
    assert_equal(ctx, 1, len(inner.failures), "exactly one failure")
    assert_equal(ctx, "ASSERT_NEAR", inner.failures[0].assertion, "assertion label")
    assert_contains(ctx, "tolerance=0.2", inner.failures[0].message, "tolerance in message")
    assert_contains(ctx, "difference=", inner.failures[0].message, "difference in message")


# ---------------------------------------------------------------------------
# String assertions
# ---------------------------------------------------------------------------

@fact
def smoke_assert_contains_passes(ctx: TestContext):
    assert_contains(ctx, "needle", "find the needle here", "substring present")


@fact
def smoke_assert_contains_fails_on_missing(ctx: TestContext):
    inner = TestContext("contains_fail_check")
    assert_contains(inner, "absent", "haystack without it", "should fail")
    assert_true(ctx, inner.has_failures, "missing substring should be a failure")


@fact
def smoke_assert_not_contains_passes(ctx: TestContext):
    assert_not_contains(ctx, "absent", "haystack without it", "substring absent")


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

@fact
def smoke_text_artifact_writes_and_is_addressable(ctx: TestContext):
    content = '{"status": "ok", "test": "smoke_text_artifact_writes_and_is_addressable"}\n'
    assert_true(ctx, ctx.write_text_artifact("summary", content), "artifact write should succeed")
    artifact_path = ctx.artifact_directory / "summary.txt"
    assert_true(ctx, artifact_path.exists(), "artifact file should exist")
    assert_equal(ctx, content, artifact_path.read_text(encoding="utf-8"), "artifact content round-trips")


@fact
def smoke_json_artifact_writes_structured_data(ctx: TestContext):
    data = {"score": 0.598, "cases": [1, 2, 3], "pass": True}
    assert_true(ctx, ctx.write_json_artifact("result", data), "JSON artifact write should succeed")
    artifact_path = ctx.artifact_directory / "result.json"
    assert_true(ctx, artifact_path.exists(), "JSON artifact should exist")


@fact
def smoke_diff_artifact_captures_divergence(ctx: TestContext):
    original = "def verify(pw, encoded):\n    return False, False\n"
    modified = "def verify(pw, encoded):\n    if pw is None: return False, False\n    return True, False\n"
    assert_true(ctx, ctx.write_diff_artifact("patch_diff", original, modified), "diff artifact write")
    artifact_path = ctx.artifact_directory / "patch_diff.txt"
    assert_true(ctx, artifact_path.exists(), "diff artifact file should exist")
    content = artifact_path.read_text(encoding="utf-8")
    assert_contains(ctx, "---", content, "diff should have header lines")
    assert_contains(ctx, "+++", content, "diff should have header lines")


@fact
def smoke_capture_stdout_artifact_captures_output(ctx: TestContext):
    with ctx.capture_stdout("captured_output"):
        print("hello from inside capture_stdout")
        print("second line")

    artifact_path = ctx.artifact_directory / "captured_output.txt"
    assert_true(ctx, artifact_path.exists(), "captured stdout artifact should exist")
    content = artifact_path.read_text(encoding="utf-8")
    assert_contains(ctx, "hello from inside capture_stdout", content, "first line captured")
    assert_contains(ctx, "second line", content, "second line captured")


# ---------------------------------------------------------------------------
# Theory
# ---------------------------------------------------------------------------

@theory
def smoke_theory_supports_named_cases(ctx: TestContext):
    cases = [
        {"name": "zeros",         "left": 0,  "right": 0,  "expected": 0},
        {"name": "small-positive","left": 2,  "right": 3,  "expected": 5},
        {"name": "mixed-sign",    "left": 5,  "right": -2, "expected": 3},
    ]

    def check(c: TestContext, case: dict):
        assert_equal(c, case["expected"], case["left"] + case["right"],
                     "theory cases reuse assertion logic across named rows")

    ctx.run_theory_cases(cases, check)


@theory
def smoke_theory_display_name_includes_case(ctx: TestContext):
    cases = [{"name": "alpha", "val": 1}, {"name": "beta", "val": 2}]

    observed_names: list[str] = []

    def capture_name(c: TestContext, case: dict):
        observed_names.append(c.display_name)

    ctx.run_theory_cases(cases, capture_name)

    assert_equal(ctx, 2, len(observed_names), "both case names captured")
    assert_contains(ctx, "[alpha]", observed_names[0], "first case display name")
    assert_contains(ctx, "[beta]",  observed_names[1], "second case display name")


# ---------------------------------------------------------------------------
# xfail
# ---------------------------------------------------------------------------

@fact(xfail=True)
def smoke_xfail_is_expected_to_fail(ctx: TestContext):
    assert_true(ctx, False, "this intentionally fails — should show as [XFAIL]")


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------

@benchmark(iterations=128)
def smoke_benchmark_runs(bctx: BenchmarkContext):
    _ = bctx.iteration + 1


@fact
def smoke_benchmark_is_in_registry(ctx: TestContext):
    names = [b.name for b in _BENCHMARK_REGISTRY]
    assert_true(ctx, "smoke_benchmark_runs" in names, "benchmark should be registered")


@fact
def smoke_benchmark_is_not_in_test_registry(ctx: TestContext):
    names = [t.name for t in _TEST_REGISTRY]
    assert_false(ctx, "smoke_benchmark_runs" in names, "benchmark should not appear as a test")


@fact
def smoke_benchmark_execute_returns_structured_results(ctx: TestContext):
    results = execute_benchmarks("smoke_benchmark_runs")
    assert_equal(ctx, 1, len(results), "filter should select one benchmark")
    assert_equal(ctx, "smoke_benchmark_runs", results[0].name, "result name matches")
    assert_equal(ctx, 128, results[0].iterations, "iteration count preserved")
    assert_true(ctx, results[0].elapsed_ns > 0, "elapsed time should be positive")
    assert_true(ctx, results[0].avg_ns >= 0, "avg_ns should be non-negative")


# ---------------------------------------------------------------------------
# Doom
# ---------------------------------------------------------------------------

@doom_fact
def sibyl_doom_abort_in_child(ctx=None):
    foretell_doom("Intentional sys.exit to validate subprocess doom containment.")
    sys.exit(1)


@fact
def smoke_doom_case_is_registered(ctx: TestContext):
    names = [dc.name for dc in _DOOM_REGISTRY]
    assert_true(ctx, "sibyl_doom_abort_in_child" in names,
                "doom case should be discoverable by name")


@fact
def smoke_doom_envelope_recovered(ctx: TestContext):
    assert_doom(ctx, "sibyl_doom_abort_in_child", __file__, 0)

    summary_found = any(
        "sibyl_doom_abort_in_child_summary" in str(p)
        for p in ctx.artifact_paths
    )
    breadcrumb_found = any(
        "sibyl_doom_abort_in_child_breadcrumb" in str(p)
        for p in ctx.artifact_paths
    )
    assert_true(ctx, summary_found, "summary artifact should be attached to test context")
    assert_true(ctx, breadcrumb_found, "breadcrumb artifact should be attached to test context")


# ---------------------------------------------------------------------------
# Runner entrypoint when executed directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from sibyl.harness import run_all_tests, write_summary_json
    import os
    os.chdir(Path(__file__).resolve().parent)
    exit_code, records = run_all_tests()
    write_summary_json(records)
    sys.exit(exit_code)
