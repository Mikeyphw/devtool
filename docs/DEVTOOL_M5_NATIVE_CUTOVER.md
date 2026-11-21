# M5 — Native ZIP/PYZ delivery, launcher-optional cutover

Target source: `Mikeyphw/devtool` `main` commit `178000fa5c6d38fc3e0c32be9a913b5b9e69cfc8` (tree `b5f255e01b610214401c86af9a7e479d12d8048d`). M5 implementation acceptance was recorded against feature commit `df5aa7e4734f135937f939a00c83ac0fcaeee38b` through isolated Termux native ZIP/PYZ parity, hosted PR checks and source-bound CI24. The source HEAD after this documentation update requires its own hosted and CI24 requalification.

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

- Ordinary ZIP supports `inspect` and `check` using the existing native overlay validator. For **apply**, M5 requires a complete native schema-v3 `delivery_contract` *before any child execution*. Schema v3 enables automatic M3 post-commit adoption; the M5 ZIP adapter resolves the session from durable native history and the checksummed journal rather than requiring a terminal `NATIVE_M3_SESSION_ID` line. Legacy schema-v1/v2 ZIPs remain inspectable/checkable but M5 refuses to apply them; use their original explicitly approved ABI. For v3 ZIP apply, the existing native engine owns transaction, validation and M3 adoption. Post-commit ZIP sessions are resolved from exactly one source-bound, verified native history entry and one checksum-validated delivery journal, not from a terminal `NATIVE_M3_SESSION_ID` line (Rich summaries can omit it). Missing, ambiguous or drifted evidence remains an uncertain state: preserve history, inspect and recover; never reapply.
- Verified v3 PYZ first passes `read_embedded_package`, which checks exact bootstrap bytes, manifest, source/payload digests and member integrity. Then it invokes the existing v3 Python launcher `check` or `apply` without `--approve-remote`. The launcher remains **optional** for native ZIP; older v1/v2 immutable bootstraps remain byte-for-byte unchanged.
- Legacy or unrecognized PYZs have read-only `inspect` only, with no execution. To apply them, use their original approved ABI; M5 does not synthesize trust.
- `status`, `resume`, `report`, `deliver`, `ci` use a durable native delivery session, not an artifact filename. Delivery mutation authorization is a separate decision from authentication.

## Terminal summary

The new plain summary provides source branch/SHA, requested/executed/skipped test *record availability* (never invent zero or PASS), native transaction/history/commit/validation, session, PR URL/recorded HEAD, historic CI scope, unresolved steps, release/device not performed. JSON is available with Devtool's universal `--json` flag. The adapter does not copy to clipboard, use a full-screen TUI, or enable M6 Rich Live. Native child CLI receives `--no-clipboard` and plain presentation.

## M5 source-bound acceptance — Termux and GitHub

**Acceptance evidence scope:** implementation commit `df5aa7e4734f135937f939a00c83ac0fcaeee38b` of PR #40. The evidence below is not automatically transferable to a subsequent documentation-only commit. After this document is updated by a native commit, requalify the nine hosted PR workflows **and** dispatch/verify a new CI24 bound to the new exact HEAD before changing draft/merge status. Do not repeat already committed ZIP/PYZ applications.

| Gate | Required evidence | Recorded result for implementation HEAD |
|---|---|---|
| Packaging | Source/byte inventory, SHA-256, validation declaration | PASS — source-pinned native ZIP/PYZ archive checks; integrity and declarative contracts verified |
| Focused regression | Parser/generated surfaces, ZIP/PYZ, legacy, CLI summaries | PASS — Termux native focused regression and offline strict tests recorded for durable-session correction |
| Native ZIP | `validate-overlay → apply → commit → status/resume` | PASS — isolated commit `95c69aeb69d4eb479a9601b77ff53b0676b32f2f`, session `b948bafa68a2463dbc61ad851db1153e` |
| Verified PYZ | Integrity → `check → apply → committed session` | PASS — isolated commit `6e3629c8aea9b9cfae8d3f5987ba69366013c161`, session `8f87a17b56454194a584479ff1f2fd7f` |
| Session recovery | Unique history-bound session; read-only `resume`, no overlay replay | PASS — ZIP session recovered after Rich output omitted the session marker; history, commit, transaction and journal verified |
| Hosted PR | Exact pushed HEAD and required PR jobs | PASS — PR #40, nine required workflows successful for implementation commit |
| CI24 | 24 shards, 25 jobs, 25 artifact digests, summary archive | PASS — run [44841802062](https://github.com/Mikeyphw/devtool/actions/runs/44841802062), attempt 1, `PASS_CI24_VERIFIED`, non-release |
| Release/device | Separate qualification outside M5 | Not claimed; independent gates remain outstanding |

The original native ZIP apply succeeded before the operator M5 adapter reported an uncertain session. The correction in implementation commit `df5aa7e` resolves that committed ZIP via a unique canonical native history entry and checksum-validated M3 delivery journal, not by scraping a terminal session ID. ZIP was never reapplied during recovery. PYZ was tested in a separate private Git clone. Neither parity gate performed GitHub push/PR/CI dispatch, merge or device/release changes.

The enormous generated `devtool/core_cli_schema.json` textual diff has an independent JSON comparison showing only the lengths of `commands`, `parser.subparsers.children` and `options` changed. The final documentation operator also requires all three lists to be **append-only** with all pre-existing values identical. This is a separate, read-only semantic audit, not a waiver of canonical parser and generated-surface tests.

No v3.2 launcher retirement before **both** native ZIP/PYZ parity, observed durable sessions and hosted acceptance. M6 follows M5 acceptance, not before: portrait-first Rich Live console shared by overlay operations and the existing GitHub CI monitor (`delivery_rich.py`, `NativeRichMonitor`, `ci_snapshot_view`, `actions_run_view`). M7 Credential Doctor, M8 event continuation, M9 federated delivery.
