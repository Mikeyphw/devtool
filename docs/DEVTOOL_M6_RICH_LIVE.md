# M6 — Unified native Rich Live console (Termux-first)

M6 is a **presentation-only** migration of native Devtool overlay lifecycle and
GitHub Actions/required-CI observations onto a shared portrait-first terminal
renderer. The implementation is an exact-source M6 candidate until Termux,
PTY/SSH/tmux and hosted PR/CI acceptance are recorded. Neither application of
an overlay nor display of a provider's green check qualifies a release.

## Interactive behavior

`devtool delivery session ci` and `devtool actions watch` retain the **same**
`NativeRichMonitor` and the native bounded registrar/provider observations, but
render a single-column stack: identity → stages → events → output → evidence →
keyboard hints. `devtool apply overlay` and the native overlay lifecycle use the
same `devtool.m6_live` components, fed only by existing artifact lifecycle
callbacks. This layer never polls GitHub, creates a journal, performs Git
mutations or validates native results. Native stdout/stderr and exit codes are
never hidden or fabricated. No alternate screen or Textual host is used.

On real interactive TTYs (including Termux/tmux/SSH) `Rich.Live` runs with
`screen=False`, `transient=False`, `auto_refresh=False`,
`redirect_stdout=False`, `redirect_stderr=False`. Keyboard state restoration,
resize, and explicit log-fetch remain owned by the existing monitor. On non-TTY,
JSON, `CI`, `NO_COLOR`, `CLICOLOR=0` or dumb-terminal paths, the native plain
and machine output is authoritative. Existing `DEVTOOL_EXECUTION_PRESENTATION`
/ `DEVTOOL_JOB_UI` plain selectors and `DEVTOOL_TEXTUAL_*_HOST=0` remain valid
and do not silently replace stdout with a full-screen UI.

### Keys (GitHub CI/Actions foreground monitor)

- `j/k` and arrows: select existing observed job/workflow.
- `Enter`: show job steps or selected run details.
- `v`: toggle additional receipt metadata.
- `l`: explicitly fetch source-verified GitHub logs via the **existing** native
  provider; no automatic download or token leakage.
- `[`/`]`: page already-retrieved, sanitized log text.
- `r`: consume the current bounded native poll interval early, not a new watcher.
- `q`: detach UI without qualifying CI, canceling work, retrying or reapplying.

Operator read-only views keep their existing `h`/back and `g/w/l` controls;
M6 reuses the same portrait terminal chrome without changing those operations.

## Evidence and credentials

The required-CI adapter reuses exact source, branch, repository, and session
identities from its already-read canonical delivery journal; this does not
create another GitHub request or change the provider verdict.

The UI never infers a commit, session, source SHA, percentage or timestamp from
an activity indicator. Missing evidence explicitly reads `not recorded`. Only
real native lifecycle events or provided provider/job/step receipts are rendered.
The visible event window is bounded; durable logs and native provider receipts
remain unchanged. GitHub App credential information is **only** projected if
already supplied by the native M4 diagnostic source and only through a public
field allowlist. These are distinct: authenticated, authorized for operation,
remote verified and required CI qualified. Tokens and private keys are never
shown or copied. Existing `DEVTOOL_UI_ICONS` preference supports `nerd`,
`unicode`, `ascii` via the same Devtool configuration; no new undocumented flag.

### Native safety requirements

- The UI is not a second source of truth for transaction/CI verdicts.
- M6 does not auto-apply, redispatch, push, create PRs, merge or release.
- Unknown/missing/failed/skipped states stay explicitly unresolved.
- External terminal controls are stripped and secrets redacted before rendering;
  Rich `Text` is used instead of interpreting provider data as markup.
- Terminal redraw failures print a warning but do not alter native apply verdict.
- Native command output and all diagnostics remain visible and independently
  available in existing logs. No clipboard operation is performed by M6.

## Acceptance and staged rollout

Tests: `tests/test_m6_live.py`, extended `tests/test_delivery_rich.py`,
`tests/test_operator_rich.py`, `tests/test_native_delivery_m5.py`, and existing
`tests/test_rich_progress_layout.py`. The source-only private export, native
launcher-v3 check, native apply and session/commit are necessary but not a
substitute for hosted PR checks or CI24 on the exact M6 HEAD.

Termux acceptance must separately prove portrait 40/52/60/80 columns, actual
PTY resize and tmux/SSH, Nerd Font/fallback, ZIP and verified PYZ operation
with non-hidden original output, and CI pending→PASS/FAIL plus deliberate log
inspection. Native machine receipts and UI evidence must agree. Until that
proof, the existing Textual path remains a compatibility fallback. M6 does
**not** remove M5 launcher compatibility, change required CI policy, or retire
older executable bootstraps.

M7 Credential Doctor, M8 event continuation and M9 federation are separate.

## V2 M5 presenter compatibility

Run identity preserves `Attempt 2` and `Source abcd` exact plain-text labels,
including narrow Termux layouts. This fixes the legacy M5 monitor regression
without changing provider evidence or native execution. V2 builds in a fresh
isolated worktree and retains V1 preflight artifacts for inspection.
