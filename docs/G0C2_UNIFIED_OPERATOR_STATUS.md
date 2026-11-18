# Devtool G0 C2 — Native operator status, unified projection

C2 is a read-only presentation layer over existing native Devtool receipts. The
native overlay engine owns atomic apply/transaction/validation/commit,
`delivery_session` owns source binding and remote mutation intents,
`delivery_ci_policy`/`delivery_qualification` own exact PR CI/24-shard
qualifications, and the independent Actions journal owns dispatch correlation.
This implementation creates **no second state machine or mutation authority**.

## Operator interfaces

- **Plain CLI:** `devtool --no-clipboard --ui plain --repo PATH delivery session status --id SESSION`.
  Reports the same structured `operator_status` rendered by other interfaces,
  with a native session/transaction ID, source and package hashes, PR and CI
  runs/attempts, blockers, and next safe action. Never treats `PLANNED` as a
  failed native apply; it reads `native_apply` independently.
- **JSON:** Append `--json` to receive the original backward-compatible
  delivery session fields **plus** `operator_status` with schema
  `devtool.operator-status.v1`. Non-TTY and machine output contains no ANSI
  progress glyphs or new clipboard behavior.
- **Rich:** On a capable interactive terminal, the existing `operator_rich`
  renderer displays the same schema via inline Rich, not a Textual/alternate
  screen; `NO_COLOR`, `CLICOLOR=0`, `TERM=dumb`, CI, JSON, and pipes retain
  plain semantics.
- **Textual:** Explicitly open `devtool control-center` or the explicit Textual
  TUI. The main Dashboard reads its newest source-bound local session from the
  same projector and shows source/package, transaction, session, PR, checks,
  regression, blockers and next safe action. The provider performs no Actions
  dispatch, GitHub mutation, or remote API requests; it never auto-opens a UI.
- **Live CI qualification:** `delivery session report --id SESSION
  --regression-request-id REQUEST` invokes the **existing** read-only B3
  verifier. Only a source-matched B3 `PASSED` scope may appear as live-qualified.
  Saved CI receipts alone are labelled `RECORDED_NEEDS_LIVE_RECHECK`.
  Neither the dashboard nor `status` invents provider evidence.

## Safety and schema

`operator_status` includes `target`, `package`, `transaction`, `session`,
`pr`, `ci.required`, `ci.regression`, `ci.overall_acceptance`,
`ci.release_qualification`, `ci.device_qualification`, bounded native `events`,
`failures`, `blockers` (`code` + `reason`), `next_safe_action`, and
`remote_mutation_performed=false`.

The `source_binding` is read from the native delivery session. Existing recorded
success is not a substitute for a new exact-source/attempt/summary/artifact
recheck. Unknown, absent, drifted, tampered, or ambiguous evidence remains
blocking. Even a fully qualified pre-merge candidate is
`PENDING_POST_MERGE_MAIN_HEAD` until a separate main-head qualification. Release
and device claims are always `NOT_PERFORMED` in this projection.

The Control Center may display the status, but all existing potentially
mutating controls remain behind their original dispatcher and confirmation
modals. No new remote action button is introduced here.

## Milestone boundary and acceptance

C2 does **not** implement C3 compatibility retirement or G4 release
qualification. No 24-shard workflow is dispatched by C2 or its delivery
command. Run full repository 24-shard hosted regression **only when all C1/C2/C3
are finished**, per the operator's request. Native Termux C2 apply/PR checks
remain distinct from local pytest and mocked Textual verification.
