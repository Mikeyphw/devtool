# UX-RICH01 · Native Rich delivery and GitHub Actions monitor

Devtool's *existing* GitHub Actions, CI-registration, native overlay and
session components remain the authorities. `devtool.delivery_rich` is a
read-only projection, never a second polling engine, delivery engine, state
journal, remote mutation layer or CI qualification evaluator.

## Default terminal experience

On a genuine interactive TTY, native `delivery session ci` and
`devtool actions watch` display an inline Rich Live view. When invoked through
the launcher's byte-preserving stdout/stderr relay, its native child output
remains plain instead of incorrectly claiming the pipe is a TTY. The one-step
UX-RICH01 handoff explicitly resumes the native **post-commit** session for a
real Rich watch, without replaying the overlay. Textual/alternate-screen
hosts remain disabled and `FORCE_COLOR=1` never overrides redirection safety.

The UI displays exact workflow and run IDs, current statuses, required/missing
checks, running/passed/failed state, selected run and attempt. Run watching
also displays provider-observed job and step status. There are **no guessed
completion percentages**, fabricated successes, asynchronous GitHub mutations,
automatic clipboard copies, implicit retries, device operations or release claims.

Keyboard when a real local POSIX terminal is available:

- `j/k` or arrows: select observed workflow/job
- `Enter`: expand or collapse details/steps
- `l`: explicitly fetch source-verified remote logs and open paged viewer
- `v`: toggle metadata verbosity
- `r`: request the next native CI or individual-run poll immediately within its original bounded checks
- `q`: detach from the foreground monitor, preserving session and remote run

The layout is responsive at 40, 52, 80 and 120 columns. No shell clipboard
host is used. Logs and full native diagnostics remain in the existing
failure-transcript and Devtool's own durable CI receipt. A detached watcher
must be resumed using the same session; it never qualifies CI.

## Plain and machine paths

JSON output, redirected stdout/stderr, dumb terminals, `CI=true`, `NO_COLOR`
or `CLICOLOR=0` retain the existing plain/machine behavior. Original
`DEVTOOL_EXECUTION_PRESENTATION=plain`, `DEVTOOL_JOB_UI=plain`, and both
`DEVTOOL_TEXTUAL_*_HOST=0` remain mandatory for launcher execution.

The existing `DEVTOOL_ACTIONS_STREAM=1` machine observation framing retains
precedence over Rich, preserving the Textual host's external source contract.

## Required-CI added in the same overlay

When source HEAD truly lacked `config/delivery-required-ci.json`, optional-CI
preflight warns. If the **native overlay itself adds** that policy, the launcher
verifies the *committed resulting policy* through native `load_policy` before
running CI. Existing malformed, staged, dirty or mismatched policy still
fails prior to the native apply. If the new policy cannot be proven, no CI is
qualified. When `--watch-ci` is absent, remote delivery never silently implies
that CI observation occurred.

## Recovery

A check failure after native apply and push means **do not reapply**. Use the
existing native `delivery session status/report/ci` and the launcher
`status`, `report`, or `ci --session-id` on its exact pinned source/runtime.
The GitHub PR may be open and remote-verified while checks fail; this is not
a release or merge qualification.

## Acceptance and evidence

Test `tests/test_delivery_rich.py`, the existing launcher contract suite,
and other affected native/CI integration tests. Verify actual PR HEAD and
hosted workflows before claiming hosted qualification. Termux interactive
rendering and native Devtool overlay validation require execution on the
operator's source-bound checkout. Do not treat a skipped device or hosted test
as passing evidence.

## Runtime selection and remaining native-output limitations

New packages generated **after** UX-RICH01 is installed use the audited,
versioned native runtime compatibility contract described in
[Launcher runtime compatibility](LAUNCHER_RUNTIME_COMPATIBILITY.md).
Build-time runtime Git pins remain immutable provenance while the actual
same-major runtime is independently source-attested and its CLI ABI probed.

When native Devtool invokes another native CLI with real stdout/stderr, its
artifact details remain visible and are captured unchanged in the complete
failure transcript. A Rich overlay **does not replace or filter** those
actual child streams. Future compact native apply-table rendering requires
changes to the native apply engine's own structured presentation contract,
not a launcher-side interception or log suppression. The CI/runs monitor,
however, uses a bounded Rich Live view with the existing native poll/receipt
engine on a genuine TTY. Redirected output stays plain even with FORCE_COLOR.

`l` is an explicit on-demand log fetch, not a silent/automatic download.
This is distinct from CI qualification and never changes its verdict.

## UX-RICH01 completion: on-demand log archive and native compact manifest

On an interactive terminal, pressing `l` now requests the selected run's
current, exact-attempt GitHub log archive via the existing native
`actions_runs.remote_logs` operation, following explicit user input.
The archive is saved in the native private log store; the Rich panel
offers redacted, bounded text pages. `[` / `]` navigate pages; `l`
closes the log pane. GitHub run/source/attempt/workflow identity is
independently rechecked before download. Invalid/mismatched archives
fail closed; no signed URLs, tokens, or automatic clipboard writes.
Failed CI/run monitors remain inspectable until `q` exits, without
additional workflow polling or remote writes.

The native apply engine now uses its *own* existing compact Rich
artifact renderer in ordinary non-verbose terminal apply; `--verbose`
retains full panels. Native stdout/stderr and durable transaction
logs remain unchanged; no launcher-side output filtering. CI JSON,
non-TTY, no-color, and legacy package source-pin semantics remain.
