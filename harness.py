"""
Sibyl — Python test harness.

A behavioral port of Marionette (C++) with Python-native additions
for structured artifact capture, patch validation, and model output inspection.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Iterator

# ---------------------------------------------------------------------------
# Registry globals
# ---------------------------------------------------------------------------

_TEST_REGISTRY: list[TestCase] = []
_BENCHMARK_REGISTRY: list[BenchmarkCase] = []
_DOOM_REGISTRY: list[DoomCase] = []

# Path to this executable (set at startup for doom subprocess spawning)
_SIBYL_EXECUTABLE_PATH: Path | None = None

# Modules loaded via --modules (propagated to doom child subprocess)
_LOADED_MODULES: list[str] = []

# Active doom context (set only inside a doom child process)
_ACTIVE_DOOM_CONTEXT: DoomExecutionContext | None = None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Failure:
    test_name: str
    file: str
    line: int
    assertion: str
    message: str
    expected: str = ""
    actual: str = ""


@dataclass
class Skip:
    test_name: str
    file: str
    line: int
    reason: str


@dataclass
class TestCase:
    name: str
    function: Callable
    xfail: bool = False


@dataclass
class BenchmarkCase:
    name: str
    function: Callable
    iterations: int = 10_000


@dataclass
class BenchmarkResult:
    name: str
    iterations: int
    elapsed_ns: int

    @property
    def avg_ns(self) -> int:
        return self.elapsed_ns // self.iterations if self.iterations else 0


@dataclass
class DoomCase:
    name: str
    function: Callable


@dataclass
class DoomRunResult:
    launched: bool = False
    terminated_abnormally: bool = False
    exit_code: int = 0
    signal_number: int = 0
    artifact_directory: Path = field(default_factory=Path)
    breadcrumb_path: Path = field(default_factory=Path)
    stdout_path: Path = field(default_factory=Path)
    stderr_path: Path = field(default_factory=Path)
    foretelling: str = ""


@dataclass
class DoomExecutionContext:
    artifact_directory: Path
    breadcrumb_path: Path
    foretelling: str = ""


# ---------------------------------------------------------------------------
# TestContext
# ---------------------------------------------------------------------------

def _sanitize_path_component(value: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "_", value)
    return sanitized or "unnamed"


def _artifact_root() -> Path:
    root = os.environ.get("SIBYL_ARTIFACT_ROOT")
    if root:
        return Path(root)
    # Walk up from this file to find a project root heuristically,
    # then fall back to cwd.
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "out").exists() or (parent / ".git").exists():
            return parent / "out" / "test-artifacts"
    return Path.cwd() / "out" / "test-artifacts"


class TestContext:
    def __init__(self, test_name: str) -> None:
        self._test_name = test_name
        self._theory_case_name: str = ""
        self._failures: list[Failure] = []
        self._artifact_paths: list[Path] = []
        self._skip: Skip | None = None

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def test_name(self) -> str:
        return self._test_name

    @property
    def display_name(self) -> str:
        if self._theory_case_name:
            return f"{self._test_name}[{self._theory_case_name}]"
        return self._test_name

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    @property
    def failures(self) -> list[Failure]:
        return self._failures

    @property
    def artifact_paths(self) -> list[Path]:
        return self._artifact_paths

    @property
    def skip_state(self) -> Skip | None:
        return self._skip

    @property
    def has_failures(self) -> bool:
        return bool(self._failures)

    @property
    def is_skipped(self) -> bool:
        return self._skip is not None

    @property
    def artifact_directory(self) -> Path:
        return _artifact_root() / _sanitize_path_component(self.display_name)

    # ------------------------------------------------------------------
    # Theory case management
    # ------------------------------------------------------------------

    def _enter_theory_case(self, case_name: str) -> None:
        self._theory_case_name = case_name

    def _leave_theory_case(self) -> None:
        self._theory_case_name = ""

    def run_theory_cases(
        self,
        cases: list[dict[str, Any]],
        case_function: Callable[[TestContext, dict[str, Any]], None],
    ) -> None:
        """
        Iterate over a list of case dicts. Each dict must have a "name" key.
        The case_function receives (context, case_dict).
        """
        for case in cases:
            case_name = str(case.get("name", "unnamed"))
            self._enter_theory_case(case_name)
            try:
                case_function(self, case)
            finally:
                self._leave_theory_case()

            if self.is_skipped:
                return

    # ------------------------------------------------------------------
    # Failure recording
    # ------------------------------------------------------------------

    def record_failure(
        self,
        file: str,
        line: int,
        assertion: str,
        message: str,
        expected: str = "",
        actual: str = "",
    ) -> None:
        self._failures.append(Failure(
            test_name=self.display_name,
            file=file,
            line=line,
            assertion=assertion,
            message=message,
            expected=expected,
            actual=actual,
        ))

    def skip(self, file: str, line: int, reason: str) -> None:
        self._skip = Skip(
            test_name=self.display_name,
            file=file,
            line=line,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Artifact helpers
    # ------------------------------------------------------------------

    def _ensure_artifact_directory(self) -> Path | None:
        directory = self.artifact_directory
        try:
            directory.mkdir(parents=True, exist_ok=True)
            return directory
        except OSError as exc:
            self.record_failure(
                __file__, 0, "WRITE_ARTIFACT",
                f"failed to create artifact directory: {exc}",
                str(directory), str(exc),
            )
            return None

    def write_text_artifact(self, artifact_name: str, content: str) -> bool:
        """Write a plain-text artifact. Returns True on success."""
        directory = self._ensure_artifact_directory()
        if directory is None:
            return False

        path = directory / (_sanitize_path_component(artifact_name) + ".txt")
        try:
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            self.record_failure(
                __file__, 0, "WRITE_TEXT_ARTIFACT",
                f"failed to write artifact: {exc}",
                str(path), str(exc),
            )
            return False

        self._artifact_paths.append(path)
        return True

    def write_json_artifact(self, artifact_name: str, obj: Any) -> bool:
        """Serialize obj to JSON and write as an artifact."""
        directory = self._ensure_artifact_directory()
        if directory is None:
            return False

        path = directory / (_sanitize_path_component(artifact_name) + ".json")
        try:
            path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
        except (OSError, TypeError) as exc:
            self.record_failure(
                __file__, 0, "WRITE_JSON_ARTIFACT",
                f"failed to write JSON artifact: {exc}",
                str(path), str(exc),
            )
            return False

        self._artifact_paths.append(path)
        return True

    def write_diff_artifact(
        self,
        artifact_name: str,
        original: str,
        modified: str,
        fromfile: str = "original",
        tofile: str = "modified",
    ) -> bool:
        """Generate a unified diff between two strings and write as artifact."""
        diff_lines = list(difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
        ))
        content = "".join(diff_lines) if diff_lines else "(no differences)\n"
        return self.write_text_artifact(artifact_name, content)

    def assert_patch_applies(
        self,
        patch_text: str,
        repo_path: Path | str,
        message: str,
        *,
        file: str = "",
        line: int = 0,
    ) -> bool:
        """
        Check that patch_text applies cleanly to repo_path via git apply --check.
        Records a failure if it does not. Returns True on clean apply.
        """
        import tempfile
        repo_path = Path(repo_path)
        with tempfile.NamedTemporaryFile(suffix=".patch", mode="w",
                                         encoding="utf-8", delete=False) as tmp:
            tmp.write(patch_text)
            tmp_path = Path(tmp.name)

        try:
            result = subprocess.run(
                ["git", "apply", "--check", str(tmp_path)],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        if result.returncode == 0:
            return True

        self.record_failure(
            file or __file__, line, "ASSERT_PATCH_APPLIES",
            message,
            "patch applies cleanly",
            (result.stderr or result.stdout or "git apply failed").strip(),
        )
        return False

    @contextmanager
    def capture_stdout(self, artifact_name: str) -> Iterator[None]:
        """
        Context manager: captures all stdout written inside the block
        and writes it as a text artifact automatically on exit.
        """
        buffer = StringIO()
        old_stdout = sys.stdout
        sys.stdout = buffer
        try:
            yield
        finally:
            sys.stdout = old_stdout
            self.write_text_artifact(artifact_name, buffer.getvalue())


# ---------------------------------------------------------------------------
# Assertion helpers (called by decorator-based assertions)
# ---------------------------------------------------------------------------

def _caller_location(depth: int = 2) -> tuple[str, int]:
    """Return (file, line) of the call site `depth` frames up."""
    frame = sys._getframe(depth)
    return frame.f_code.co_filename, frame.f_lineno


def assert_true(ctx: TestContext, condition: bool, message: str) -> None:
    if not condition:
        f, l = _caller_location()
        ctx.record_failure(f, l, "ASSERT_TRUE", message, "True", "False")


def assert_false(ctx: TestContext, condition: bool, message: str) -> None:
    if condition:
        f, l = _caller_location()
        ctx.record_failure(f, l, "ASSERT_FALSE", message, "False", "True")


def assert_equal(ctx: TestContext, expected: Any, actual: Any, message: str) -> None:
    if expected != actual:
        f, l = _caller_location()
        ctx.record_failure(f, l, "ASSERT_EQUAL", message, repr(expected), repr(actual))


def assert_not_equal(ctx: TestContext, expected: Any, actual: Any, message: str) -> None:
    if expected == actual:
        f, l = _caller_location()
        ctx.record_failure(f, l, "ASSERT_NOT_EQUAL", message, f"not {repr(expected)}", repr(actual))


def assert_near(
    ctx: TestContext,
    expected: float,
    actual: float,
    tolerance: float,
    message: str,
) -> None:
    difference = abs(expected - actual)
    if difference <= tolerance:
        return
    f, l = _caller_location()
    ctx.record_failure(
        f, l, "ASSERT_NEAR",
        f"{message}, tolerance={tolerance}, difference={difference}",
        repr(expected),
        repr(actual),
    )


def assert_contains(ctx: TestContext, needle: str, haystack: str, message: str) -> None:
    """Assert that needle is a substring of haystack."""
    if needle not in haystack:
        f, l = _caller_location()
        ctx.record_failure(
            f, l, "ASSERT_CONTAINS", message,
            f"substring: {repr(needle)}",
            f"not found in: {repr(haystack[:200])}{'...' if len(haystack) > 200 else ''}",
        )


def assert_not_contains(ctx: TestContext, needle: str, haystack: str, message: str) -> None:
    """Assert that needle is NOT a substring of haystack."""
    if needle in haystack:
        f, l = _caller_location()
        ctx.record_failure(
            f, l, "ASSERT_NOT_CONTAINS", message,
            f"substring absent: {repr(needle)}",
            f"found in: {repr(haystack[:200])}{'...' if len(haystack) > 200 else ''}",
        )


def assert_sequence_equal(
    ctx: TestContext,
    expected: list,
    actual: list,
    message: str,
) -> None:
    for i, (e, a) in enumerate(zip(expected, actual)):
        if e != a:
            f, l = _caller_location()
            ctx.record_failure(
                f, l, "ASSERT_SEQUENCE_EQUAL",
                f"{message} (mismatch at index {i})",
                repr(e), repr(a),
            )
            return
    if len(expected) != len(actual):
        f, l = _caller_location()
        ctx.record_failure(
            f, l, "ASSERT_SEQUENCE_EQUAL",
            f"{message} (sequence length mismatch)",
            f"len={len(expected)}", f"len={len(actual)}",
        )


def fail(ctx: TestContext, message: str) -> None:
    f, l = _caller_location()
    ctx.record_failure(f, l, "FAIL", message)


def skip(ctx: TestContext, reason: str) -> None:
    f, l = _caller_location()
    ctx.skip(f, l, reason)


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def fact(fn: Callable | None = None, *, xfail: bool = False):
    """
    Register a test fact.

    Usage:
        @fact
        def my_test(ctx):
            assert_true(ctx, True, "ok")

        @fact(xfail=True)
        def known_broken(ctx):
            ...
    """
    def decorator(f: Callable) -> Callable:
        _TEST_REGISTRY.append(TestCase(name=f.__name__, function=f, xfail=xfail))
        return f

    if fn is not None:
        # Called as @fact (no parens)
        return decorator(fn)
    # Called as @fact(...) with keyword args
    return decorator


def theory(fn: Callable | None = None, *, xfail: bool = False):
    """
    Alias for @fact. Use with ctx.run_theory_cases() for data-driven tests.
    """
    return fact(fn, xfail=xfail)


def benchmark(fn: Callable | None = None, *, iterations: int = 10_000):
    """
    Register a benchmark.

    Usage:
        @benchmark
        def my_bench(bctx):
            ...

        @benchmark(iterations=500)
        def my_fine_bench(bctx):
            ...
    """
    def decorator(f: Callable) -> Callable:
        _BENCHMARK_REGISTRY.append(BenchmarkCase(
            name=f.__name__, function=f, iterations=iterations
        ))
        return f

    if fn is not None:
        return decorator(fn)
    return decorator


def doom_fact(fn: Callable) -> Callable:
    """
    Register a doom case — a function expected to crash/abort.
    Must be paired with assert_doom() in a @fact.
    """
    _DOOM_REGISTRY.append(DoomCase(name=fn.__name__, function=fn))
    return fn


# ---------------------------------------------------------------------------
# Doom infrastructure
# ---------------------------------------------------------------------------

def foretell_doom(message: str) -> bool:
    """
    Record a foretelling message before an expected crash.
    Only meaningful inside a doom child subprocess.
    """
    global _ACTIVE_DOOM_CONTEXT
    if _ACTIVE_DOOM_CONTEXT is None:
        return False

    _ACTIVE_DOOM_CONTEXT.foretelling = message
    try:
        # Open with line buffering (buffering=1) and explicit flush + os.fsync
        # so the write survives sys.exit() without going through atexit handlers.
        import os as _os
        with open(_ACTIVE_DOOM_CONTEXT.breadcrumb_path, "a", encoding="utf-8", buffering=1) as f:
            f.write(f"foretell: {message}\n")
            f.flush()
            _os.fsync(f.fileno())
        return True
    except OSError:
        return False


def set_sibyl_executable_path(path: Path) -> None:
    global _SIBYL_EXECUTABLE_PATH
    _SIBYL_EXECUTABLE_PATH = path


def _is_doom_case_registered(name: str) -> bool:
    return any(dc.name == name for dc in _DOOM_REGISTRY)


def run_doom_case_in_child(case_name: str, artifact_directory: Path) -> int:
    """
    Execute a doom case in-process (called from child subprocess).
    Returns an exit code.
    """
    global _ACTIVE_DOOM_CONTEXT

    try:
        artifact_directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 31

    breadcrumb_path = artifact_directory / "doom-breadcrumb.txt"
    try:
        with open(breadcrumb_path, "w", encoding="utf-8") as f:
            f.write(f"doom-case: {case_name}\n")
            f.write("child-stage: entered\n")
            f.flush()
    except OSError:
        return 32

    meta_path = artifact_directory / "doom-meta.txt"
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(f"doom-case={case_name}\n")
            f.write("child-entered=1\n")
            f.flush()
    except OSError:
        return 34

    function = next((dc.function for dc in _DOOM_REGISTRY if dc.name == case_name), None)
    if function is None:
        return 35

    ctx = DoomExecutionContext(
        artifact_directory=artifact_directory,
        breadcrumb_path=breadcrumb_path,
    )
    _ACTIVE_DOOM_CONTEXT = ctx

    try:
        function()
    except SystemExit as exc:
        _ACTIVE_DOOM_CONTEXT = None
        code = exc.code if isinstance(exc.code, int) else 1
        with open(meta_path, "a", encoding="utf-8") as f:
            f.write("child-returned=0\n")
        return code
    except Exception:
        _ACTIVE_DOOM_CONTEXT = None
        traceback.print_exc()
        return 1
    finally:
        _ACTIVE_DOOM_CONTEXT = None

    with open(meta_path, "a", encoding="utf-8") as f:
        f.write("child-returned=1\n")
    return 0


def run_doom_case_subprocess(case_name: str, artifact_directory: Path) -> DoomRunResult:
    """
    Spawn this executable as a child process to run a doom case.
    Captures stdout/stderr to files in artifact_directory.
    """
    result = DoomRunResult(
        artifact_directory=artifact_directory,
        breadcrumb_path=artifact_directory / "doom-breadcrumb.txt",
        stdout_path=artifact_directory / "stdout.txt",
        stderr_path=artifact_directory / "stderr.txt",
    )

    try:
        artifact_directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return result

    executable = _SIBYL_EXECUTABLE_PATH
    if executable is None:
        return result

    cmd = [
        sys.executable, str(executable),
        "--doom-case", case_name,
        "--doom-artifact-dir", str(artifact_directory),
    ]
    if _LOADED_MODULES:
        cmd += ["--modules"] + _LOADED_MODULES

    result.launched = True
    try:
        proc = subprocess.run(
            cmd,
            capture_output=False,
            stdout=open(result.stdout_path, "w"),
            stderr=open(result.stderr_path, "w"),
            timeout=60,
        )
        result.exit_code = proc.returncode
        result.terminated_abnormally = proc.returncode != 0
    except subprocess.TimeoutExpired:
        result.terminated_abnormally = True
        result.exit_code = -1
    except OSError:
        result.launched = False
        return result

    # Parse foretelling from breadcrumb
    try:
        breadcrumb_text = result.breadcrumb_path.read_text(encoding="utf-8")
        token = "foretell: "
        idx = breadcrumb_text.find(token)
        if idx != -1:
            start = idx + len(token)
            end = breadcrumb_text.find("\n", start)
            result.foretelling = (
                breadcrumb_text[start:end] if end != -1 else breadcrumb_text[start:]
            )
    except OSError:
        pass

    return result


def assert_doom(
    ctx: TestContext,
    doom_case_name: str,
    file: str = "",
    line: int = 0,
) -> None:
    """
    Assert that a doom case terminates abnormally and produces the diagnostic envelope.
    """
    if not _is_doom_case_registered(doom_case_name):
        ctx.record_failure(
            file or __file__, line, "ASSERT_DOOM",
            f"doom case '{doom_case_name}' is not registered",
            "registered", "not found",
        )
        return

    artifact_dir = ctx.artifact_directory / f"doom_{_sanitize_path_component(doom_case_name)}"
    result = run_doom_case_subprocess(doom_case_name, artifact_dir)

    if not result.launched:
        ctx.record_failure(
            file or __file__, line, "ASSERT_DOOM",
            "doom subprocess did not launch",
            "launched", "not launched",
        )
        return

    has_foretelling = bool(result.foretelling)
    has_breadcrumb = result.breadcrumb_path.exists()
    has_stdout = result.stdout_path.exists()
    has_stderr = result.stderr_path.exists()
    has_envelope = has_foretelling and has_breadcrumb and has_stdout and has_stderr

    if not result.terminated_abnormally or not has_envelope:
        actual = (
            f"terminated_abnormally={result.terminated_abnormally}, "
            f"has_foretelling={has_foretelling}, "
            f"has_breadcrumb={has_breadcrumb}, "
            f"has_stdout={has_stdout}, "
            f"has_stderr={has_stderr}"
        )
        ctx.record_failure(
            file or __file__, line, "ASSERT_DOOM",
            "doom envelope was not recovered",
            "terminated abnormally with diagnostic envelope",
            actual,
        )

    # Persist doom artifacts onto the parent test context
    summary = (
        f"doom-case={doom_case_name}\n"
        f"terminated-abnormally={result.terminated_abnormally}\n"
        f"exit-code={result.exit_code}\n"
        f"signal={result.signal_number}\n"
        f"foretelling={result.foretelling}\n"
        f"artifact-directory={result.artifact_directory}\n"
    )
    ctx.write_text_artifact(f"doom_{doom_case_name}_summary", summary)

    for label, path in [
        (f"doom_{doom_case_name}_breadcrumb", result.breadcrumb_path),
        (f"doom_{doom_case_name}_stdout", result.stdout_path),
        (f"doom_{doom_case_name}_stderr", result.stderr_path),
    ]:
        try:
            ctx.write_text_artifact(label, path.read_text(encoding="utf-8"))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _matches_filter(name: str, filter_str: str) -> bool:
    return not filter_str or filter_str in name


def _print_failure(failure: Failure) -> None:
    print(f"  FAIL {failure.test_name} at {failure.file}:{failure.line} [{failure.assertion}]")
    print(f"    message:  {failure.message}")
    if failure.expected or failure.actual:
        print(f"    expected: {failure.expected}")
        print(f"    actual:   {failure.actual}")


def _print_skip(skip: Skip) -> None:
    print(f"  SKIP {skip.test_name} at {skip.file}:{skip.line}")
    print(f"    reason: {skip.reason}")


def _print_artifacts(ctx: TestContext) -> None:
    for path in ctx.artifact_paths:
        print(f"    artifact: {path}")


def run_all_tests(filter_str: str = "") -> tuple[int, list[dict]]:
    """
    Run all registered tests. Returns (exit_code, summary_records).
    exit_code is 0 if all non-xfail tests passed.
    """
    tests = sorted(_TEST_REGISTRY, key=lambda t: t.name)

    executed = passed = failed = skipped = xfailed = xpassed = 0
    total_failures = 0
    summary_records: list[dict] = []

    for test in tests:
        if not _matches_filter(test.name, filter_str):
            continue

        executed += 1
        ctx = TestContext(test.name)

        try:
            test.function(ctx)
        except _SkipSignal as sig:
            ctx.skip(__file__, 0, sig.reason)
        except Exception as exc:
            f_tb = traceback.format_exc()
            ctx.record_failure(
                __file__, 0, "UNCAUGHT_EXCEPTION",
                f"test raised {type(exc).__name__}: {exc}",
                "no exception",
                f_tb,
            )

        record: dict[str, Any] = {"name": test.name, "xfail": test.xfail}

        if ctx.is_skipped:
            skipped += 1
            record["result"] = "skip"
            record["reason"] = ctx.skip_state.reason  # type: ignore[union-attr]
            print(f"[SKIP] {test.name}")
            _print_skip(ctx.skip_state)  # type: ignore[arg-type]
            _print_artifacts(ctx)

        elif ctx.has_failures:
            total_failures += len(ctx.failures)
            if test.xfail:
                xfailed += 1
                record["result"] = "xfail"
                print(f"[XFAIL] {test.name}")
            else:
                failed += 1
                record["result"] = "fail"
                record["failures"] = [
                    {
                        "assertion": f.assertion,
                        "message": f.message,
                        "expected": f.expected,
                        "actual": f.actual,
                        "file": f.file,
                        "line": f.line,
                    }
                    for f in ctx.failures
                ]
                print(f"[FAIL] {test.name}")
                for failure in ctx.failures:
                    _print_failure(failure)
            _print_artifacts(ctx)

        else:
            if test.xfail:
                xpassed += 1
                record["result"] = "xpass"
                print(f"[XPASS] {test.name}  (expected failure but passed — investigate)")
            else:
                passed += 1
                record["result"] = "pass"
                print(f"[PASS] {test.name}")
            _print_artifacts(ctx)

        summary_records.append(record)

    print(
        f"\nSummary: {executed} test(s), "
        f"{passed} passed, "
        f"{skipped} skipped, "
        f"{failed} failed, "
        f"{xfailed} xfailed, "
        f"{xpassed} xpassed, "
        f"{total_failures} assertion failure(s)"
    )

    exit_code = 0 if (failed == 0 and xpassed == 0) else 1
    return exit_code, summary_records


def execute_benchmarks(filter_str: str = "") -> list[BenchmarkResult]:
    benchmarks = sorted(_BENCHMARK_REGISTRY, key=lambda b: b.name)
    results: list[BenchmarkResult] = []

    for bench in benchmarks:
        if not _matches_filter(bench.name, filter_str):
            continue

        ctx = BenchmarkContext(iteration=0)
        start = time.perf_counter_ns()
        for i in range(bench.iterations):
            ctx.iteration = i
            bench.function(ctx)
        elapsed = time.perf_counter_ns() - start

        results.append(BenchmarkResult(
            name=bench.name,
            iterations=bench.iterations,
            elapsed_ns=elapsed,
        ))

    return results


@dataclass
class BenchmarkContext:
    iteration: int = 0


def run_benchmarks(filter_str: str = "") -> int:
    results = execute_benchmarks(filter_str)
    for r in results:
        print(
            f"[BENCH] {r.name}  "
            f"iterations={r.iterations}  "
            f"elapsed_ns={r.elapsed_ns}  "
            f"avg_ns={r.avg_ns}"
        )
    print(f"\nBenchmark Summary: {len(results)} benchmark(s)")
    return 0


def write_summary_json(summary_records: list[dict], root: Path | None = None) -> Path:
    """
    Write out/test-artifacts/summary.json. Returns the path written.
    """
    out_root = root or _artifact_root()
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / "summary.json"

    totals = {"pass": 0, "fail": 0, "skip": 0, "xfail": 0, "xpass": 0}
    for r in summary_records:
        totals[r.get("result", "fail")] = totals.get(r.get("result", "fail"), 0) + 1

    payload = {"totals": totals, "tests": summary_records}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Skip signal (used by skip() helper to unwind the test cleanly)
# ---------------------------------------------------------------------------

class _SkipSignal(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
