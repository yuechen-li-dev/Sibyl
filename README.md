# Sibyl

A lightweight Python test harness. Behavioral port of Marionette (C++) with Python-native additions for structured artifact capture, patch validation, and model output inspection.

No dependencies outside the standard library.

---

## Quick start

```
sibyl/
├── sibyl/
│   ├── __init__.py
│   └── harness.py
├── sibyl_run.py
├── smoke_tests.py
└── out/
    └── test-artifacts/   ← written at runtime
```

Write tests in any `.py` file, import them via `--modules`, run via `sibyl_run.py`:

```sh
python sibyl_run.py --modules my_tests
python sibyl_run.py --modules my_tests other_tests   # multiple modules
python sibyl_run.py --modules my_tests substring_filter  # filter by name
```

Tests run alphabetically. Exit code is 0 if all tests pass (skips and expected failures don't count).

---

## Declaring tests

### `@fact`

The basic test unit. Receives a `TestContext` as its only argument.

```python
from sibyl import fact, assert_true, assert_equal

@fact
def my_test(ctx):
    assert_true(ctx, 1 + 1 == 2, "math works")
    assert_equal(ctx, "expected", "expected", "strings match")
```

Assertions are **non-fatal** — all failures are collected and reported together. The test does not stop at the first failure.

### `@theory`

Alias for `@fact`. Conventionally used with `ctx.run_theory_cases()` for data-driven tests.

```python
from sibyl import theory, assert_equal

@theory
def addition_cases(ctx):
    cases = [
        {"name": "zeros",        "left": 0, "right": 0,  "expected": 0},
        {"name": "small-positive","left": 2, "right": 3,  "expected": 5},
        {"name": "mixed-sign",   "left": 5, "right": -2, "expected": 3},
    ]

    def check(c, case):
        assert_equal(c, case["expected"], case["left"] + case["right"],
                     "addition should be correct")

    ctx.run_theory_cases(cases, check)
```

Each case dict **must** have a `"name"` key. Failures report as `addition_cases[mixed-sign]`.

### `@fact(xfail=True)`

Marks a test as expected to fail. Shows as `[XFAIL]` when it fails (expected) and `[XPASS]` if it unexpectedly passes. `[XPASS]` causes a non-zero exit.

```python
@fact(xfail=True)
def known_broken_case(ctx):
    assert_equal(ctx, 1, 2, "this is known to be wrong for now")
```

---

## Assertions

All assertion helpers take `ctx` as the first argument and a `message` string as the last. None of them raise — they record into `ctx.failures`.

| Helper | Checks |
|---|---|
| `assert_true(ctx, condition, msg)` | `condition` is truthy |
| `assert_false(ctx, condition, msg)` | `condition` is falsy |
| `assert_equal(ctx, expected, actual, msg)` | `expected == actual` |
| `assert_not_equal(ctx, expected, actual, msg)` | `expected != actual` |
| `assert_near(ctx, expected, actual, tolerance, msg)` | `abs(expected - actual) <= tolerance` |
| `assert_contains(ctx, needle, haystack, msg)` | `needle in haystack` |
| `assert_not_contains(ctx, needle, haystack, msg)` | `needle not in haystack` |
| `assert_sequence_equal(ctx, expected, actual, msg)` | element-wise equality, length match |
| `fail(ctx, msg)` | unconditional failure |
| `skip(ctx, reason)` | skip this test with a reason |

`skip()` exits the test immediately (like `return`). Use it for preconditions:

```python
@fact
def requires_network(ctx):
    if not network_available():
        skip(ctx, "no network in this environment")
    # test continues only if network is available
```

### `ctx.assert_patch_applies(patch_text, repo_path, message)`

Runs `git apply --check` against `repo_path`. Records a failure with the git error output if the patch doesn't apply cleanly.

```python
@fact
def patch_applies_to_repo(ctx):
    patch = open("my.patch").read()
    ctx.assert_patch_applies(patch, "/path/to/repo", "patch should apply cleanly")
```

---

## Artifacts

Artifacts are files written to `out/test-artifacts/<TestName>/`. Their paths are printed after each test completes. Use them to capture evidence — diffs, model outputs, structured results — that you want to inspect after a run without scraping stdout.

### `ctx.write_text_artifact(name, content) → bool`

```python
ctx.write_text_artifact("response", model_output)
# writes: out/test-artifacts/my_test/response.txt
```

### `ctx.write_json_artifact(name, obj) → bool`

Serializes `obj` with `json.dumps(indent=2)`.

```python
ctx.write_json_artifact("scores", {"func_pass": 0.6, "sec_pass": 0.19})
# writes: out/test-artifacts/my_test/scores.json
```

### `ctx.write_diff_artifact(name, original, modified, fromfile, tofile) → bool`

Generates a unified diff between two strings.

```python
ctx.write_diff_artifact(
    "patch_vs_golden",
    golden_implementation,
    model_patch,
    fromfile="golden",
    tofile="model",
)
# writes: out/test-artifacts/my_test/patch_vs_golden.txt
```

### `ctx.capture_stdout(artifact_name)` — context manager

Captures all stdout written inside the block and writes it as a text artifact automatically.

```python
@fact
def model_output_captured(ctx):
    with ctx.capture_stdout("model_response"):
        result = call_model("implement this feature")
        print(result)  # captured

    # artifact written regardless of what happened inside
```

---

## Benchmarks

Benchmarks live in a separate registry and are never run as tests.

```python
from sibyl import benchmark, BenchmarkContext

@benchmark(iterations=500)
def my_benchmark(bctx: BenchmarkContext):
    _ = bctx.iteration * 2
```

```sh
python sibyl_run.py --modules my_tests --bench          # all benchmarks
python sibyl_run.py --modules my_tests --bench my_bench # filtered
```

`execute_benchmarks(filter)` returns a list of `BenchmarkResult(name, iterations, elapsed_ns, avg_ns)` for use inside tests:

```python
from sibyl import fact, execute_benchmarks, assert_true

@fact
def benchmark_completes_quickly(ctx):
    results = execute_benchmarks("my_benchmark")
    assert_true(ctx, results[0].avg_ns < 1_000_000, "should be under 1ms per iteration")
```

---

## Doom module

For testing code that is **expected to crash or exit abnormally**. The doom harness spawns the test binary as a child subprocess and validates that it terminated abnormally and left a full diagnostic envelope.

This is intentionally quarantined. Don't use it for ordinary negative tests.

### `@doom_fact`

Registers a function that is expected to terminate abnormally.

```python
from sibyl import doom_fact, foretell_doom
import sys

@doom_fact
def my_crash_case():
    foretell_doom("intentional crash to test envelope recovery")
    sys.exit(1)
```

### `foretell_doom(message)`

Writes a message to the breadcrumb file before the crash. This is what proves the crash was intentional and the code reached the expected point.

### `assert_doom(ctx, doom_case_name)`

Spawns the doom case as a subprocess and asserts:
- It launched
- It terminated abnormally
- The diagnostic envelope is complete: foretelling + breadcrumb + stdout + stderr

All four artifacts are attached to the parent test context.

```python
from sibyl import fact, assert_doom

@fact
def crash_envelope_recovered(ctx):
    assert_doom(ctx, "my_crash_case")
    # Artifacts from the crash are now in ctx.artifact_paths
```

### Operational requirement

Doom requires `sibyl_run.py` as the entrypoint (not direct module execution), because the parent needs to be able to re-spawn itself as a child. When using `--modules`, those modules are automatically forwarded to the doom child subprocess.

---

## CLI reference

```sh
# Run all tests from a module
python sibyl_run.py --modules my_tests

# Run multiple modules
python sibyl_run.py --modules my_tests other_tests

# Filter to tests whose name contains a substring
# (filter is a positional arg after all --flags)
python sibyl_run.py --modules my_tests -- auth_

# Run benchmarks
python sibyl_run.py --modules my_tests --bench
python sibyl_run.py --modules my_tests --bench my_bench_name

# List registered tests
python sibyl_run.py --modules my_tests --list

# List registered benchmarks
python sibyl_run.py --modules my_tests --list-bench
```

Output is written to `out/test-artifacts/summary.json` after every test run.

---

## Environment variable

`SIBYL_ARTIFACT_ROOT` overrides the artifact output directory. Default is `out/test-artifacts/` relative to the nearest `.git` root or current working directory.

---

## Output format

```
[PASS] test_name
[FAIL] test_name
  FAIL test_name at path/to/file.py:42 [ASSERT_EQUAL]
    message:  values should match
    expected: 'secure'
    actual:   'insecure'
    artifact: out/test-artifacts/test_name/response.txt
[SKIP] test_name
  SKIP test_name at path/to/file.py:15
    reason: precondition unavailable
[XFAIL] test_name      ← expected failure, counts as passing
[XPASS] test_name      ← unexpected pass, counts as failing

Summary: 12 test(s), 9 passed, 1 skipped, 1 failed, 1 xfailed, 0 xpassed, 2 assertion failure(s)
```

---

## Anti-patterns (carried over from Marionette)

- Don't use benchmarks as pass/fail correctness gates.
- Don't use doom for ordinary negative tests — it's for abnormal termination only.
- Don't bypass assertions with silent logging-only checks.
- Don't skip writing artifacts when debugging replay mismatches — capture bounded evidence.
