# G0 R4 — Regression closure and native validation ownership

Source-bound to PR #29 (`5a3c5997acc61dbdca97369032cd907f405a2513`,
tree `f369f4d1792407ecc1a0d7a1b06131364348c297`).

Consolidates all R3 storage, session, cleanup, Android and terminal fixes
without removing tests or relaxing private XDG/0700 constraints, symlink
refusals, receipt integrity, rollback or reservation safety.

R4 additionally repairs the CLI regression where normalized `validate abort`
used the generic exclusive guard and conflicted with its own pending apply
transaction. The validation-management commands now use the same scoped
transaction-aware lock as resumed validation and continue to reject any
unrelated concurrent operation. The existing real-CLI regression is preserved.

Native validation runs focused Python safety/functional tests only, with
visible progress and a bounded timeout. The full repository regression suite
runs exclusively via the 24 hosted shards after approved delivery. There is
no Termux Gradle, Android build, device write, release or auto-merge stage.

Pre-delivery QA: affected source transforms and CLI behavior were exercised
against the provided main snapshot plus SHA-verified PR29 CLI reconstruction.
This is not an assertion of exact-tree native validation or hosted acceptance.
