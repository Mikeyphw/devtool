# UX-RICH01 completion — compact native apply and verified in-monitor logs

This milestone closes two UX-RICH01 limitations without replacing the native
Devtool apply, commit, durable-session, GitHub, CI, or release authorities.

## Native apply

The existing native `summary.render_artifact_manifest` now supports an explicit
`compact` rendering parameter. In regular non-verbose terminal apply sessions,
`cli._show_and_confirm_artifact_manifest` selects the compact renderer at *any*
terminal width. `--verbose` retains full multi-panel artifact contents, policy,
lifecycle and repository layout. No stdout/stderr filtering, hiding subprocess
output, copying to clipboard or altered mutation/validation behavior occurs.
The complete artifact/receipt can still be inspected with native Devtool.

## Run and PR monitor logs

During interactive source-bound `delivery session ci` or `actions watch`, `l`
explicitly requests the currently selected GitHub run's *full log archive*.
The native `actions_runs.remote_logs` operation downloads and retains the archive
in its private state directory without exposing authenticated redirects or
GitHub secrets. A new read-only adapter rechecks the exact remote run ID,
source SHA, current attempt, event, workflow and repository before retrieval.
The archive SHA-256 is verified independently and its text entries are read
without filesystem extraction, with ZIP traversal and decompression bounds.

The terminal shows bounded, redacted log lines with `[` / `]` paging; the native
archive remains available for explicit operator inspection. A credentialed
provider may refuse access; that refusal stays visible, never becomes a CI
pass, and does not trigger a rerun. A failed workflow remains on screen for
inspection until `q`; this does not poll again, cancel jobs or alter CI outcome.
The log viewer enforces size and text-line limits for resource safety. Large
archives may be downloaded but refuse inline rendering rather than silently
truncating or pretending complete log coverage.

## Source and native requirements

The overlay is exact-HEAD/tree/blob/payload bound and must be built, validated,
applied/committed, pushed and qualified only through the native launcher.
`DEVTOOL_EXECUTION_PRESENTATION=plain`, `DEVTOOL_JOB_UI=plain` and
`DEVTOOL_TEXTUAL_*_HOST=0` remain required for launch execution. Rich on a
real TTY is separate from Textual. JSON, pipes, `TERM=dumb`, `NO_COLOR` and
`CLICOLOR=0` never enable an interactive monitor or plaintext log fetch.
Legacy immutable package runtime pinning, 3.x ABI compatibility, session
recovery, branch security and required-CI checks are not relaxed.
