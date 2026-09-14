# Roadmap Promise Recheck Report — Overlay 15 hotfix v2

## Result

Status: **PASS after hotfix v2**.

This report is the explicit promise-by-promise recheck requested after both target-specific Overlay 15 seals passed. The audit was performed against the final repo state from the handoff plus Overlay 14, Overlay 15A, and Overlay 15B.

## Check 1 of 3 — roadmap assignment inventory

- Handoff roadmap total: 15 overlays.
- Deduped audit inventory: 308 findings.
- Assigned findings in Overlays 1-14: 308.
- Duplicate owners found: 0.
- Missing finding IDs found: 0.
- Overlay 15 finding count: 0 new findings; final qualification only.

Hotfix v2 upgrades `FINAL_REMEDIATION_COVERAGE.json` to schema v2 and records every finding ID, overlay owner, and overlay title. The repository-wide final seal now validates exact finding IDs instead of accepting only aggregate `308/308` counters.

## Check 2 of 3 — repo evidence and seal reports

- Required roadmap reports are present, including Overlay 14 language-runner/packaging evidence and both Overlay 15 target reports.
- Required regression test files are present for every roadmap slice.
- `devtool final-seal --strict` remains the repository-wide release gate.
- `devtool final-seal --target devtool --strict` remains the Python/root target seal.
- `devtool final-seal --target android_hud --strict` remains the Android HUD target seal.

## Check 3 of 3 — runnable validation wiring

Gap found: the previous Overlay 15 target reports named the final-seal checks, and the Android apply command explicitly passed Gradle tasks, but the `final-seal` validation profile did not itself append a runnable target final-seal `CommandSpec`. That made the Android seal weaker than promised because the profile flag was declarative evidence, not a planned validation step.

Hotfix v2 fixes that by appending:

- `python-final-seal` for the `devtool` target.
- `android-final-seal` for the `android_hud` target.

The Android target also declares an explicit `seal` task set in `.devtool.toml`:

- `:app:assembleDebug`
- `:app:testDebugUnitTest`
- `:app:lintDebug`

Instrumentation remains controlled by the existing `when-device-present` Android validation policy, so the proper device gate is attempted when a device is available and documented as skipped when no device is available.

## Answer to “why didn't devtool android have a proper seal task?”

Because the earlier Overlay 15B implementation relied on two things: a side-effect-free target report (`devtool final-seal --target android_hud --strict`) and explicit Gradle tasks in the apply command. It did not add a runner-planned final-seal step to the `final-seal` validation profile, and `.devtool.toml` did not have a named Android `seal` task set. That was an implementation gap. Hotfix v2 closes it.

## Final promise status

After hotfix v2, every roadmap promise has either direct implementation evidence, regression/contract tests, final-seal coverage, or target-specific validation wiring. No unresolved promise gap remains in the repo evidence known to this audit.
