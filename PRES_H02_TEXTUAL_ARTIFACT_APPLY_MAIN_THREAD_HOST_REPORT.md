# PRES-H02 — Textual artifact-apply main-thread host hotfix

PRES-H02 is an in-place incident correction and consumes no new full-plan position.
It follows qualified/applied PRES-R01 v2 and PRES-H01, and precedes the rebuilt
PRES-R02 qualification attempt.

## Runtime defect

Native Termux proved that Textual's Linux terminal driver cannot run from the helper
thread previously used by normal execution presentation. PRES-H01 correctly failed
closed to plain output, but that meant `apply-overlay`, `apply-overlays`, and
`apply-snapshot` had no interactive Textual surface at all. The failed PRES-R02 v1
run made this visible while artifact validation printed ordinary terminal output.

## Delivered boundary

- A human interactive artifact apply is intercepted before canonical CLI execution.
- The parent/main Python process owns the real Textual application and terminal
  signal lifecycle.
- The complete canonical Devtool invocation runs in a managed child process using
  the same CLI arguments/source and remains sole authority for artifact
  qualification, transactions, validation, rollback/recovery, commit, RUN evidence,
  logs, and exit status.
- The child receives a stable run id and continues publishing the existing Tasker
  live-state files. The Textual parent reads those files and does not create a
  second TUI-specific runtime store.
- Child stdout/stderr is captured into a bounded Textual `RichLog`.
- The host displays artifact identity/review, canonical status/phase/progress, and
  live output.
- Ctrl+C uses a Textual confirmation modal and signals the managed child process
  group. Canonical rollback/recovery remains Devtool-owned.
- Parent-confirmed application forwards `--yes` only to the managed child so no
  worker/background process tries to own terminal input.
- `DEVTOOL_AUTO_HUD_CHILD=1` prevents the child from spawning a competing detached
  HUD renderer; Tasker state publication remains enabled.
- JSON/machine, redirected/non-TTY, CI, verbose/trace, explicit plain, quiet, and
  explicit stdout-payload modes bypass this host and preserve ordinary output
  contracts.

## Qualification

The overlay owns a Devtool-native `pres-h02` workflow with source syntax, host
contracts, retained artifact/R01 regressions, and a real Textual `App.run_test()`
proof. Native validation sets `DEVTOOL_PRES_H02_REQUIRE_TEXTUAL=1`, so the concrete
Textual test may not skip on Termux.

## Still open

PRES-H02 closes artifact-apply terminal hosting only. General invoke/validation
normal-execution Textual hosting remains reopened. PRES-R02 v1 is still invalid and
must be rebuilt after this hotfix; its confirmation-modal failure is not waived.


## Native Textual compatibility correction (v2)

The first native-Termux qualification exposed an unsupported stylesheet assumption: Textual 8.2.8 rejects TCSS `@media` rules. The app therefore failed during stylesheet parsing before the managed child could start. v2 removes the unsupported media rule and implements narrow-terminal behavior through the real Screen class on mount/resize (`narrow` below 70 columns). The mandatory real `App.run_test()` proof now starts at 64x22, verifies the narrow class, resizes to 100x30, and verifies the class is removed. This preserves responsive behavior without depending on unsupported TCSS syntax.


## Native lifecycle/timer correction (v3)

The second native-Termux qualification exposed two real timing assumptions in the
Textual host proof. The fake canonical source emits a compact terminal state after
its detailed `artifact validation` phase, so the UI replaced the useful phase text
with a generic `Canonical apply lifecycle` placeholder before the Pilot assertion.
Separately, Textual may deliver one final interval tick while the default Screen is
being torn down, at which point querying a detached `#apply-status`/`#apply-log`
widget raises `NoMatches`. v3 preserves the last meaningful canonical phase when a
terminal state omits phase metadata, retains/stops the polling timer explicitly on
completion and unmount, and treats a teardown-time `NoMatches` as normal shutdown.
The mandatory real `App.run_test()` proof therefore exercises the same fast final
state and terminal resize path without depending on an accidental timer ordering.
