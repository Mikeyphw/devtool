# ANDROID-R36-R1: Textual/Rich markup boundary and GitHub-runner CI negative

Base commit: `b992006265f98c887a9a5f89ac8335c2740db134`; Git tree: `99853dc45943f99fd2c6d5c16243dcee125ad6cf`.

## Changes

- R36 negative-host regression clears inherited `GITHUB_ACTIONS`, `RUNNER_OS`, `RUNNER_ARCH` before expecting failure. Positive Linux GitHub and rejected ARM64 remain tested.
- `styled_job_line` returns a real Rich `Text` object, preserves safe ANSI styling, and renders only a small, balanced allowlist of inline Rich styles. Invalid/unknown bracket content remains literal and never reaches Textual's string-markup parser. The original on-disk log is not changed or reinterpreted as execution truth.
- Live Textual apply context, activity, phase, notice, status, compact job labels and normal execution snapshots use Rich `Text` objects instead of parsing arbitrary strings. The raw log keeps its bytes; the Textual log view can render reviewed styles without exposing tag syntax.
- Host UI rendering is separate from transaction/validation outcome; a UI projection error cannot count as a successful test or release qualification.

## Targeted qualification

Overlay uses existing `validation.tests` (pinned `devtool-test-requirements.lock`, plus the project-owned `tui` extra) and keeps the Python self-overlay autovalidation disabled to avoid stale installed-runner planning. No local Gradle. Negative tests include an untrusted `[/etc/passwd]` path, bracketed filenames, bad tags, ANSI escapes, `NO_COLOR`, GitHub environment inheritance and existing R32/R34/security regression. The `tui` extra is required for real Textual widget acceptance rather than silently skipping the UI tests.

Required acceptance: Termux overlay target tests, R36 hosted source CI on the *new SHA*, full CI24 on the *same SHA*, R34 non-release hosted DAG backward compatibility. New R36 semantic consumer acceptance remains separately OPEN, as do release, device and signer authority.
