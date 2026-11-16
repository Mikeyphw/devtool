# UX-RICH03 — Persistent native Rich operator workspace

UX-RICH03 adds a **foreground, inline Rich workspace** over existing Devtool
read-only authorities. It is not an execution engine, state machine, remote
scheduler, CI observer, release qualification gate, or alternate transaction
mechanism. No direct GitHub/SSH mutation, overlay application, clipboard action,
or background daemon is performed by the workspace.

## Entry points

- `devtool actions ui`: foreground Rich workspace in a genuine interactive TTY.
  Keeps UX-RICH02's explicit read-only Actions GET shortcuts and all non-UI
  Actions commands' existing semantics.
- `devtool tui`: when **all four** plain-host environment selectors are active,
  opens this Rich workspace rather than the full-screen Textual host. Without
  this opt-in, the existing TUI command behavior is preserved.
- For noninteractive, piped, `--json`, machine, CI, and no-color contexts,
  existing plain/JSON outputs remain unchanged; no hidden terminal host is used.

These four launcher selectors are mandatory for delivery and for the Rich-only
`tui` path:

```sh
export DEVTOOL_EXECUTION_PRESENTATION=plain
export DEVTOOL_JOB_UI=plain
export DEVTOOL_TEXTUAL_APPLY_HOST=0
export DEVTOOL_TEXTUAL_EXECUTION_HOST=0
```

The terminal respects `NO_COLOR`, `CLICOLOR=0`, `TERM=dumb` and the existing
native Rich TTY detection. Rich Live uses `screen=False`, `transient=False`,
`auto_refresh=False`. The terminal is restored in a `finally` block after detach or error. Key
sequences which are incomplete or unrecognized are inert.

## Workspace navigation

| Key | Action |
|---|---|
| `1` | Actions — exact-source local dispatch and pipeline receipts |
| `2` | Runs — native RunCatalog (`create=False`) |
| `3` | Validation — current native validation session and phases |
| `4` | SSH — remote worker sessions, **disabled by default** |
| `j` / `k`, arrow up/down | Select native row within active tab |
| `Enter` | Expand selected record details (source data only) |
| `r` | Re-read current tab (SSH requires prior opt-in or `g`) |
| `a` | Toggle bounded automatic observation; **on SSH tab**, explicitly opt in/out of SSH polling |
| `g` | Explicit read-only SSH GET in SSH tab; native Actions review GET in Actions tab |
| `w` / `l` | Native read-only Actions workflows/lineage GET |
| `b` | Return from a remote Actions detail |
| `q` | Detach without cancelling any native work |

Automatic local snapshots refresh at most every 2 seconds. SSH is **never
queried automatically** until the operator presses `a` on the SSH tab, and its
read interval is at least 12 seconds; explicit `g` submits one request. SSH
status reads execute in one nonblocking daemon worker, with no overlapping GET;
the request may still take longer than 12 seconds because that value is a
**refresh cadence**, not a provider timeout. Other tabs and keyboard navigation
remain usable while that request is pending. Only native
functions `actions_workspace.local_recent`, `RunCatalog.open(create=False)`,
`validation_sessions.load_active_session/status_payload`, and
`ssh_history.sessions_payload` / `resolve_worker_profile` produce snapshots.
Output streams are not intercepted or reinterpreted. Running CLI commands and
SSH follower commands outside the workspace preserve native stdout/stderr.

## Source, session and UI persistence

The active repository HEAD is bound at workspace entry. Every observation
verifies the HEAD before and after native reads; HEAD checks continue at a
bounded cadence even when automatic refresh is paused. Explicit network GETs
always check the current HEAD first. If it moves, automatic reads stop, previous data is
marked stale, and the user must reopen to bind the new source. Run selection follows unique native identity across snapshot reorder; when
it disappears or an identity collides, selection resets. Display names alone
cannot authorize a selection. An API/receipt error never becomes a success and never erases
the last snapshot without a visible error.

Only the selected tab is persisted in the user's private XDG state directory
(`$XDG_STATE_HOME/devtool/operator-ui`, otherwise `~/.local/state/devtool/operator-ui`),
with owner-only permissions and atomic replacement. It is scoped to canonical
repository path + exact source HEAD and contains no run ID, approval, token,
password, stdout, secret, or authority. No UI files are written under `.devtool`
or the repository. This file is a preference, **not** a durable Devtool session.
Native delivery/validation/run/SSH receipts remain the only recovery authorities.

## Scope and evidence limits

This milestone provides near-real-time read-only **polling of native snapshots**,
not an event-subscription transport or a second streaming engine. In particular,
SSH snapshot GET runs in a single, detached daemon **read-only** worker only
after explicit opt-in; late results are rejected when source identity drifts;
it does not follow worker stdout or provide an interactive remote shell. CI
qualification, retries, pushes, PR creation, releases, overlay recovery, and
validation controls remain in existing commands with their native approvals.
The Rich presentation cannot turn pending, skipped, missing or failed evidence
into verified success.

## Regression and CI

`tests/test_operator_workspace.py` covers stable tab/poll behavior, no automatic
SSH provider access, explicit consent, private-state symlink and permissions,
source drift, error/stale behavior, native-identity selection, redaction,
Actions read-only GET preservation, and native read-only adapters.
The existing `tests/test_operator_rich.py`, CI registrar, native launcher
contract and pipeline projection gates remain. Hosted workflow
`devtool-launcher-v3.yml` adds the current UX-RICH03 tests and paths rather
than replacing previous coverage.

A passing mocked/offline test is not proof of native Devtool validation,
Termux physical-device acceptance, an applied commit, hosted CI or release
qualification. Verify the **actual PR HEAD** and its hosted checks after native
Devtool delivery, and resume the exact delivery session if apply committed.
