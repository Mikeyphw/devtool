# ENV-G1 — Host / language environment milestone gate

Date: 2026-09-28
Campaign position: ENV gate-window 3/3; ENV scope 3/7; full implementation plan 13/63.
Base user state audited: ENV-X01 commit `8305b8c`; ENV-X02 commit `65667cf`.
Next overlay after successful gate: `EXEC-X01` (14/63). Remaining ENV items before the next major scope: 0. Next major scope: EXEC.

## Gate purpose

ENV-G1 is a separate authoritative audit/seal of the ENV-X01 and ENV-X02 implementation window. It does not absorb later ENV-X04/G3 lifecycle or cross-substrate work. It proves the milestone from fresh processes and reconciles implementation promises against real consumers.

## Merge decision

Not merged with ENV-X02 or EXEC-X01. ENV-X02 is implementation ownership; ENV-G1 is the authoritative qualification boundary; EXEC-X01 begins execution/reuse ownership.

## Promise audit

ENV-X01 promises `ENV-P001..P015` remain delivered, with protected runtime namespace foundation `ENV-P029` consumed by X02 rather than re-owned there.

ENV-X02 delivery remains the audited set recorded in `ENV_X02_PROMISE_LEDGER.json`, including mise, direnv, Poetry, asdf (`ENV-P105..P116` ownership split) and pyenv (`ENV-P117..P127` ownership split), with later destination/lifecycle obligations preserved for ENV-X04/G3.

Gate-specific milestone proof closes the required G1 evidence for fresh-process asdf (`ENV-P115`), fresh-process pyenv (`ENV-P126`) and cross-provider non-ownership/plugin-system/fail-closed behavior (`ENV-P131`) without claiming the later cross-substrate final seals `ENV-P116`/`ENV-P127`.

## Audit loop findings and folded fixes

The first gate replay found two real X02 cross-consumer regressions and folded them before sealing:

1. **Manager-owned Python stale environment provenance.** PythonRunner treated a historical/derived `.devtool/venvs/python` option as an explicit `[python].environment` for Hatch and rejected restore planning. The canonical request now distinguishes configured provenance: stale/default state is ignored for manager-owned environments, while an actually configured environment remains a fail-closed ownership conflict. Restore qualification is action-aware so restore/bootstrap can plan before the manager exists; ordinary execution still requires manager qualification.
2. **Rust contaminated by Go environment integration.** X02 patching accidentally inserted Go environment resolution/cache logic into `RustRunner.build_specs`, causing Rust validation to call the nonexistent `_resolved_environment`. Rust now uses only Rust/Cargo environment policy again.

No third ENV-owned implementation gap was found after these fixes.

## X02 live validation-count reconciliation

The successful live ENV-X02 v3 apply reported `225 passed`, while the final X02 declaration in the committed overlay collects 270 tests. ENV-G1 does not treat the earlier summary as proof of the full closure set.

The gate therefore:

- directly verifies that committed `roadmap-env-x02` collects at least 270 tests;
- validates X02 through an **inline artifact `validation.tests` declaration** containing the exact authoritative command, avoiding reliance on a same-run newly loaded test ref;
- retains `roadmap-env-x02` and introduces `roadmap-env-g1` for post-commit replay;
- exports same-run validation-ref/config-root/count truth to `LIFE-P127` and TEST/PRES consumers rather than inventing a second ENV truth store.

## Devtool-native validation architecture

There is no `validate.sh`.

Artifact validation is owned by Devtool's v2 artifact-test protocol and runs four bounded structured checks inside the parent apply lifecycle:

1. `env-g1-x01-milestone` — 15 representative X01 host/privilege/substrate invariants.
2. `env-g1-x02-milestone` — 25 representative X02 mise/asdf/pyenv/direnv/Python/Go invariants.
3. `env-g1-cross-consumer` — the two gate-found Hatch/Rust regressions.
4. `env-g1-fresh-process` — six top-level gate tests, including an eight-test clean subprocess milestone replay, full-X02 declaration collection audit, ledger accounting, and lightweight provenance/runner-ownership regressions.

Top-level expected Devtool test accounting is **48 passed, 0 failed, 0 skipped**. The eight nested fresh-process checks are evidence inside the six gate tests and are intentionally not double-counted.

The expensive `roadmap-env-x01` and `roadmap-env-x02` implementation suites remain preserved for implementation qualification, debugging, and later reseals; ENV-G1 no longer replays package-bootstrap-heavy implementation tests merely to prove milestone invariants. This separation is deliberate gate policy, not reduced implementation coverage.

The committed `.devtool.toml` contains reusable bounded gate identities and a workflow:

`source-syntax -> {roadmap-env-g1-x01-milestone, roadmap-env-g1-x02-milestone} -> roadmap-env-g1-cross-consumer -> roadmap-env-g1`

The corrected workflow dogfood run completed **5 stages / 48 tests / 0 failures / 0 skipped / 0 warnings/errors**. On the construction host it completed in 14.92 seconds. The clean-base v2 artifact replay also executed all four structured validation suites successfully (15 + 25 + 2 + 6 = 48), but the known multi-suite projection bug displayed only the final 6 tests in the apply summary; per-suite evidence remains authoritative and the defect stays exported to TEST/PRES.

## Live v1 runtime incident and remediation

The first live ENV-G1 v1 apply (`65a52bd1187342989ed9f999633279ef`) was cancelled after roughly 21 minutes. It was not deadlocked: `roadmap-env-x01` eventually completed 437 tests in 1035.06 seconds, with many package/environment bootstrap tests taking 11–123 seconds each. X02 then ran for another 185 seconds before interruption.

The gate design was wrong: it replayed broad implementation suites whose purpose includes dependency/bootstrap repair. ENV-G1 v2 replaces those full-suite references with bounded milestone identities. Full implementation suites remain available as `roadmap-env-x01` and `roadmap-env-x02` for reseal/debug use. The live rollback succeeded, so the user repository remained on the post-X02 base.

## Cross-program incident integration

The newly reported transaction/recovery phantom-dirt and unpublished validated-commit incident is folded into M006 LIFE without changing the 63-item order. `LIFE-P123..P127` are documented in `LIFE_TRANSACTION_RECOVERY_PHANTOM_DIRT_INCIDENT_20260928.md` and `LIFE_PROMISE_EXTENSION_20260928.json`, owned by LIFE-X01/LIFE-G1 with TEST/PRES/RUN exports.

## Gate verdict

Candidate gate is sealable only if all four bounded Devtool-native artifact validation steps pass with zero failed/skipped required tests and zero target warnings. Later ENV-X04/G3 promises remain explicitly pending.
