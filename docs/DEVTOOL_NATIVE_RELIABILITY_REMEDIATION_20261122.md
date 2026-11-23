# M3/M5/M6 native delivery reliability remediation

Source target: `Mikeyphw/devtool` PR #47, `feature/devtool-r06-base-adoption`, original commit `69a0c5ee50b264de6025f7720750e55ab84e87ca`, exact tree `a7aadebc736705c55cc0a3bb767a0ed4af841c37`.

## Observed failure provenance

- DT-20261122-12: failed native validation mislabeled as postcommit adoption uncertainty. Qualified `git-commit` success is now checked prior to invoking M3 continuity. Failed validation retains its true phase and native exit status; failed/ambiguous commit remains a recovery-only condition.
- DT-20261122-13: a CI run that progressed from queued to completed was refused as `CI run changed across registration anchors`. Registration deduplication now uses immutable GitHub run identity and selects the newest observation only as a hint; exact-run and exact-attempt GET/jobs remain required to qualify CI.
- DT-20261122-14: `DEVTOOL_XDG_DIR` made artifact history use a custom state root while delivery sessions/Actions dispatch journals continued using the older default. New writes use `devtool.xdg.state_dir()`, while reads locate precisely identified, validated canonical/legacy receipts and refuse duplicates. No automatic migration or deletion of old journals.
- DT-20261122-15: M5 delegated terminal children through pipes with plain-mode environment overrides, suppressing Rich/PTY and native user input. Attached foreground Zsh/TTY is preserved. Headless execution uses bounded plain streaming, without falsely interpreting launcher text as commit evidence.

## Immutable identities and idempotency

A native transaction's durable history is different from a M3 delivery session, and a session differs from the Actions dispatch request and the exact workflow run/attempt. Preserve and correlate all identities. Never treat GitHub mutable `status`, `conclusion`, `updated_at`, PR `updated_at`, or added PR associations as identity fields. Never infer remote mutation outcome from process exit alone. Exact source SHA, workflow, event, run ID and attempt must still be verified independently.

State-root migration is intentionally read-only in this overlay. CI24 qualification-workflows receipts now share canonical Devtool state-root semantics; existing canonical and legacy receipts are discovered without relocation, duplicate receipts refuse, and a legacy CI24 record prevents a second dispatch. Journals in both locations are neither overwritten nor silently combined. When the same identity appears in multiple locations, refuse and report it; operators must reconcile provenance before any mutation. No `reset`, `clean`, speculative replay, auto-rerun, force push, merge, signing or release.

## Test and acceptance matrix

Local: source regression suite (rerun count recorded in delivery receipt) and 23 subtests across native continuity, M5 terminal, session, CI registrar/evidence and CI24 Actions dispatch, plus PTY-attached subprocess I/O/return code tests. This is not a claim about Termux, Android, GitHub live CI or physical devices.

Native target: clean exact-head PR #47 worktree, current branch+remote, exact source and tree; native check first, then explicit native apply/commit exactly once; qualified M3 session and history; PR update only with separate approval; fresh head-bound required checks and separate 24-shard CI receipts. Real Termux TTY, clipboard, security, release and device obligations remain separate gates.

## Operator considerations

The roadmap's M3/M5/M6 scope is implemented in Devtool production modules, not in a replacement transaction or GitHub engine. The transport package includes a native schema-v3 overlay and an operator that only materializes the manifest and delegates to the installed Devtool runtime. It cannot qualify a commit or an Actions run on its own. Apply is disabled when the checkout is dirty or the exact source SHA/tree are wrong. Never rerun apply after an uncertain commit; inspect native history and resume its recorded session instead.

## DT-20261122-17 · M5 nested native runtime import shadow

Native M5 previously invoked a nested `python -B -m devtool` with `cwd=target`,
which imports target source as the runtime and causes M2 to reject the valid
separate attested runtime. `native_delivery_m5._native_child_cwd` now selects
the importing controller package root *only for* nested native Devtool CLI
commands, retaining explicit `--repo` target, existing M2 independent
attestation, child stdout/stderr/exit code, and existing PYZ cwd behavior.
An adversarial subprocess regression places an invalid `devtool` package in
the target and verifies the real `devtool --help` nested child executes from
the runtime source instead. No local Gradle, unsafe worktree rewrite,
transaction replay, or remote CI qualification occurs in source tests.
