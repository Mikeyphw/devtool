# Devtool Test Count Projection Hotfix

## Scope

Focused non-gate hotfix after DEVTOOL-ROADMAP-00. It fixes structured test-count projection across the semantic UI and final CLI summary without changing runner ownership or XGO configuration.

## Contract

- Canonical structured `metadata.tests` remains authoritative.
- Legacy Go `metadata.go_test.tests` is accepted as a compatibility bridge when `metadata.tests` is absent.
- Canonical and Go compatibility counters are alternatives for one result and are never added together.
- Live `tests_*` phase metrics aggregate into `UISnapshot.progress_summary`.
- Final `completion.tests` overrides live phase totals to avoid aggregate/per-workspace double counting.
- TUI, terminal HUD, Android HUD, desktop semantic HUD, and machine UI snapshots consume the same progress-summary counters.
- Final JSON summaries expose top-level `tests`; CLI copy/final summaries consume structured payload counts before console parsing.
- Console parsing remains a compatibility fallback only.
- This is not a validation gate and performs no `pip install -e`/Devtool reinstall.

## Audit loop

1. Source/architecture audit traced runner metadata -> tasker phase progress/completion -> UISnapshot -> renderers -> CLI summary.
2. Focused regression contract: 6/6 hotfix tests passed.
3. Affected suite: 179 tests + 5 subtests passed across Go reporting, polyglot counts, HUD, semantic UI, tasker, desktop/Android UI migration, and adapters.
4. Relevant CLI copy-summary regressions: 4 passed.
5. Python compile check passed for every modified production module.
6. Duplication audit confirmed Android HUD no longer owns a separate live test-count aggregator.
7. A broader `tests/test_cli.py` sweep encountered an unrelated pre-existing mock signature failure (`tasker_progress`) in `test_apply_overlay_continues_after_successful_stale_rollback`; targeted CLI summary/copy contracts pass.

## Expected user-visible result

A Go run with 33 test cases and 10 passing packages now projects consistently as test counts in the semantic TUI/HUD/machine surfaces and final CLI summary, while keeping Go package totals separate.
