# Milestone C — Full roadmap audit and consolidated operator safety hotfix

This document audits the **current C3 PR HEAD** (`adc5ad6a87738d38ba21380b39369335050956f2`) against §7 of `DEVTOOL_NATIVE_OVERLAY_DELIVERY_ROADMAP.md`. It is **not** a release/device qualification, GitHub merge approval, or full-regression result.

## Review and actions

| Contract | Current implementation | Audit / hotfix action |
|---|---|---|
| C1 default terminal / STDOUT, Rich, non-TTY | Native default, optional Textual, Rich inline, machine JSON and clipboard opt-in are implemented with tests | Preserved; Control Center and optional Textual child runners use supported host disables and `--ui plain --no-clipboard` without synthesizing deprecated presentation variables. Private log file is preserved for explicit UI execution; shared child ANSI policy honors `NO_COLOR`, `CLICOLOR=0` and `TERM=dumb` before any automatic `FORCE_COLOR`. |
| C1 interruption and restore | Existing Textual/runtime test coverage and canonical child process owners | Retain existing controls, logs and receipts; no new process engine. Actual Termux TTY/interruption acceptance remains to be run. |
| C1 optional fzf and explicit UI | Opt-in paths remain; no automatic fzf | Preserved. |
| C2 shared status schema | `operator_status.py` exposes `devtool.operator-status.v1` from native receipts; CLI/Rich/Textual consume it | Preserved. Live CI and post-merge qualification remain separate from recorded CI. |
| C2 mutation authority / UI labels | GitHub Control Center had a **misleading `read-only` authority label** although its nested widget has confirmed dispatch/cancel/rerun. SSH worker/transaction dispatcher accepted requests without a second approval guard. | Corrected authority/visible label; required `approved is True` on dispatcher worker/rollback/recover paths after modal confirmation. No separate journal, synthetic CI pass, or implicit remote dispatch. |
| C3 immutable launchers | Old v3.1/v3.2 bootstrap preserved, new `native-terminal-v1` profile, CI tests modernized | Preserved; no launcher repin or changes to legacy profiles. |
| C3 obsolete gates | Historical provider-backed gates retain unique authenticated coverage; older assertions modernized | No deletion without replacement assurance or ruleset evidence. |

## Acceptance restrictions

- Exact-source-bound native overlay must pass Termux `validate-overlay`, `overlay check`, atomic apply, commit, and durable session adopt/resume; never reapply a committed package.
- Required PR checks and a **new** manual full-repository 24-shard attempt with all 24 shards, successful fail-closed summary, and required artifacts must be verified on the **resulting hotfix HEAD**. Do not use old C1 or pre-C3 runs to qualify it.
- No local Gradle. No auto-merge, reset/rebase, force push, automatic clipboard, or alternate-screen host.
- Post-merge actual `main` HEAD, real Termux TTY/interrupt coverage, fork live integration and device/release qualification stay explicitly unqualified until separately evidenced.
