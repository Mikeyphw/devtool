# Devtool Pytest Revitalization Report

## Scope

This overlay revitalizes the Devtool pytest suite after the Android Operations roadmap landed. It reduces avoidable wall-clock cost, makes Termux-sensitive tests explicit, and adds a reusable test-runtime layer so external toolchains can be exercised without rebuilding cold caches for every test.

## Changes

- Added `tests/_compat.py` with host detection, portable shell helpers, and session-scoped private toolchain cache locations.
- Added `tests/fake_toolchains.py` with a deterministic Go shim used by GoRunner contract tests.
- Extended `tests/conftest.py` with:
  - `requires_tool(name)` capability skip marker;
  - `requires_posix` marker;
  - `not_termux(reason)` marker;
  - `slow_external` marker;
  - `--devtool-slow-threshold` timing report;
  - `--devtool-strict-slow` budget enforcement;
  - isolated shared caches for Go, Gradle, uv, pip, npm, and Cargo.
- Converted GoRunner tests that validate Devtool behavior to the deterministic Go shim.
- Kept one real Go build as an explicit `slow_external` integration smoke.
- Converted Python subprocess-only tests to `python -S` where site packages were irrelevant.
- Avoided expensive dry-run source hashing in installer tests and dry-run installer code paths.
- Short-circuited invalid Python package import/script requests before creating a venv.
- Avoided child Python startup when project inspection points at the current interpreter.
- Reduced service-startup grace delay to a configurable value for tests that only need immediate readiness.
- Marked genuine external-tool or pseudo-tty integration tests explicitly instead of hiding them in ordinary unit tests.

## Compatibility policy

Termux compatibility is implemented as capability-based behavior, not broad skips. Tests that can be made portable through fakes, stdlib-only subprocesses, or private caches were fixed. Tests that genuinely require unavailable host features can now declare `requires_tool`, `requires_posix`, or `not_termux` with a concrete reason.

## Timing evidence

Representative improvements observed during the overlay work:

- Go executable suite: from more than 3 minutes to about 20 seconds with one real Go smoke remaining.
- GTK release qualification contract: about 6 seconds to 0.02 seconds.
- UI contract CLI contract: about 2.76 seconds to 0.07 seconds.
- Finalize JSON-hook contract: about 2.04 seconds to 0.16 seconds.
- Cross-target CLI execution contract: about 3.91 seconds to 0.19 seconds.
- Root-module packaging contract: about 4.17 seconds to 0.36 seconds.
- Python package inspection contract: about 2.17 seconds to 0.19 seconds.
- Full-diagnostics fixture: about 2.10 seconds to 0.03 seconds.
- Termux/PROot suite: 67 tests plus 7 subtests in about 1.65 seconds.
- Release manifest determinism contract: about 2.39 seconds to 0.38 seconds.
- Staged Android builder inspection: about 2.26 seconds to 0.52 seconds.
- TUI runner interrupt binding: about 2 seconds to 0.06 seconds.

## Validation

The final tree was validated in deterministic partitions because one monolithic process can exceed the execution window on constrained hosts. All collected tests were accounted for:

- 2,356 tests passed.
- 69 subtests passed.
- 0 failures.
- `python -m compileall -q devtool tests` passed.
- `devtool final-seal --strict` passed 14/14 checks.

Focused strict-slow gate:

- 362 pytest-runtime modernization tests passed with `--devtool-strict-slow`.
- Only explicitly marked `slow_external` tests exceeded the normal threshold.

## Result

The pytest suite is faster, more deterministic, and more honest on native Termux. Expensive external-tool tests are now explicit integration smokes, while ordinary contract tests avoid unnecessary compiler, site-package, venv, cache, or shell startup cost.
