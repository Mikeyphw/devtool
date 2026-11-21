# M5 — Native ZIP/PYZ delivery, launcher-optional cutover

Target source: `Mikeyphw/devtool` `main` commit `178000fa5c6d38fc3e0c32be9a913b5b9e69cfc8` (tree `b5f255e01b610214401c86af9a7e479d12d8048d`). This is a **source-only implementation candidate** until exact Termux native apply, source-correlated hosted CI and 24-shard acceptance are recorded.

## First-class entry point

`devtool native` is an operator adapter over the existing validated Devtool engines; it does not implement a new commit, network, CI, rollback or history authority.

```bash
# Read-only; never launches an unverified Python ZIP app
python3 -m devtool native inspect /path/to/source-pinned-overlay.zip --repo /path/to/worktree
python3 -m devtool native check /path/to/source-pinned-overlay.zip --repo /path/to/worktree
python3 -m devtool native check /path/to/verified-v3.pyz --repo /path/to/worktree --devtool-repo /path/to/verified-runtime

# Native change requires explicit --yes; no implicit remote approval
python3 -m devtool native apply /path/to/source-pinned-overlay.zip --repo /path/to/worktree --yes
python3 -m devtool native apply /path/to/verified-v3.pyz --repo /path/to/worktree --devtool-repo /path/to/verified-runtime --yes

# After commit, never reapply. Use its durable 32-digit session ID.
python3 -m devtool native status --session-id SESSION --repo /path/to/worktree
python3 -m devtool native resume --session-id SESSION --repo /path/to/worktree
python3 -m devtool native report --session-id SESSION --repo /path/to/worktree
python3 -m devtool native deliver --session-id SESSION --repo /path/to/worktree --auth auto --yes
python3 -m devtool native ci --session-id SESSION --repo /path/to/worktree --auth app --checks 12 --interval 15
```

`--auth app` uses the **previously configured** M4 GitHub App installation; ordinary `auto` keeps existing PAT/`gh`/SSH semantics. Explicit `--yes` is still required for remote push and PR and **not** needed for read-only CI. No automatic CI dispatch, rerun, merge, release, device write or post-commit overlay replay.

## ZIP / PYZ parity and compatibility

- Ordinary ZIP supports `inspect` and `check` using the existing native overlay validator. For **apply**, M5 requires a complete native schema-v3 `delivery_contract` *before any child execution*. This is essential because only native v3 can automatically emit `NATIVE_M3_SESSION_ID` after its commit. Legacy schema-v1/v2 ZIPs remain inspectable/checkable but M5 refuses to apply them; use their original explicitly approved ABI. For v3 ZIP apply, the existing native engine owns transaction, validation and M3 adoption. Post-commit ZIP sessions are resolved from exactly one source-bound, verified native history entry and one checksum-validated delivery journal, not from a terminal `NATIVE_M3_SESSION_ID` line (Rich summaries can omit it). Missing, ambiguous or drifted evidence remains an uncertain state: preserve history, inspect and recover; never reapply.
- Verified v3 PYZ first passes `read_embedded_package`, which checks exact bootstrap bytes, manifest, source/payload digests and member integrity. Then it invokes the existing v3 Python launcher `check` or `apply` without `--approve-remote`. The launcher remains **optional** for native ZIP; older v1/v2 immutable bootstraps remain byte-for-byte unchanged.
- Legacy or unrecognized PYZs have read-only `inspect` only, with no execution. To apply them, use their original approved ABI; M5 does not synthesize trust.
- `status`, `resume`, `report`, `deliver`, `ci` use a durable native delivery session, not an artifact filename. Delivery mutation authorization is a separate decision from authentication.

## Terminal summary

The new plain summary provides source branch/SHA, requested/executed/skipped test *record availability* (never invent zero or PASS), native transaction/history/commit/validation, session, PR URL/recorded HEAD, historic CI scope, unresolved steps, release/device not performed. JSON is available with Devtool's universal `--json` flag. The adapter does not copy to clipboard, use a full-screen TUI, or enable M6 Rich Live. Native child CLI receives `--no-clipboard` and plain presentation.

## Acceptance matrix — to be completed on the pinned device and GitHub

| Gate | Requirement | Status now |
|---|---|---|
| Packaging | Source/byte inventory, SHA-256, validation declaration | Requires local native check |
| Focused regression | Canonical parser/generated surfaces, ZIP/PYZ, legacy, CLI summaries | Requires local Termux pytest |
| Native ZIP | `validate-overlay → apply → committed session → status/resume` | NOT PERFORMED |
| Verified PYZ | Integrity → `check → apply → committed session` | NOT PERFORMED |
| Session recovery | interruption/report read-only resume, no duplicate remote POST | NOT PERFORMED |
| Hosted PR | exact pushed HEAD, required PR jobs | NOT PERFORMED |
| CI24 | 24/24 shards, 25/25 jobs, summary artifacts | NOT PERFORMED |
| Release/device | independent gates | NOT PERFORMED |

No v3.2 launcher retirement before **both** native ZIP/PYZ parity, observed durable sessions and hosted acceptance. M6 follows M5 acceptance, not before: portrait-first Rich Live console shared by overlay operations and the existing GitHub CI monitor (`delivery_rich.py`, `NativeRichMonitor`, `ci_snapshot_view`, `actions_run_view`). M7 Credential Doctor, M8 event continuation, M9 federated delivery.
