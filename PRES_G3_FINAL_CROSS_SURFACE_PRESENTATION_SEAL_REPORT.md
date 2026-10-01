# PRES-G3 — final cross-surface presentation seal

**Position:** 70/77  
**Gate window:** 1/1  
**Prerequisite:** EXEC-G3 native run `b9bff06189b04df6b6f7ac4336feb94b`, commit `87baef9`, 341 passed / 0 failed / 896 skipped, zero diagnostics.

## Fixed-point disposition

PRES-G2R intentionally left one presentation boundary open: the explicit `devtool hud --mode terminal` and `--mode studio` renderers still owned a Rich `Live` loop. They did not own normal execution truth, but a final cross-surface seal cannot leave an unqualified competing live terminal renderer.

PRES-G3 migrates that remaining live terminal lifecycle to Textual. `TerminalHud.render()` remains the canonical projection adapter and still consumes existing HUD/RUN state; only the interactive lifecycle changes. `studio` remains the web dashboard plus the same terminal projection, now hosted by Textual and with explicit HTTP-server shutdown/close when the terminal app exits. No new execution or mutation authority is introduced.

`--mode terminal --once` is deliberately static and does not invoke a live screen manager. This closes the long-standing non-interactive `TERM environment variable not set`/terminal-clear issue assigned to PRES-G3 while preserving bounded one-shot terminal output.

## Compatibility retained

- `terminal` and `v3` continue to select the terminal HUD.
- `studio`, `v5`, and `all` continue to select studio.
- web, Android bridge/app, GTK4 desktop, desktop notification, machine state, Tasker and IPC keep their existing projection contracts.
- normal invoke/validation/apply execution remains owned by the qualified Textual execution/apply hosts and canonical RUN/EXO authorities.

## Authoritative native qualification

The native workflow must run the final PRES contracts, retained HUD/cross-surface tests, the complete real Textual fixed point (R01, H02, R02, R03, both UX01 mobile/workspace tests, and the new Textual terminal HUD), generated-surface integrity, and the fresh-process no-TERM final proof.

On success, presentation finalization is closed and the campaign advances to **RMP-X01 71/77**.


## Construction fixed point

- PRES-G3 gate contracts: **6 passed**.
- Retained PRES remediation/parity slice: **57 passed, 7 real cases deselected**.
- HUD / Android / desktop / terminal cross-surface slice: **94 passed, 5 subtests passed**.
- Real Textual fixed point using the user-supplied Textual 8.2.8 bundle: **7 passed**.
- Canonical generated surfaces + release integrity: **27 passed**.
- Fresh-process `python -m devtool.pres_g3_gate --repo-root .`: **PASS** with `TERM` removed and zero Rich Live runtime refs.

Native Termux execution of `./devtoolw pres-g3` remains authoritative for qualification.


## Native v1 collection failure and v2 correction

Native Termux run `04f0bfa2f1c042fd9b7b1e082ff94ad6` failed during pytest collection before any PRES-G3 runtime stage executed. The new final-seal test used `@pytest.mark.asyncio`, but Devtool's strict pytest marker registry does not declare an `asyncio` marker. The transaction rolled back cleanly.

PRES-G3 v2 stays at **70/77** and does not alter HUD/runtime implementation. The real Textual HUD test now follows the same repository-native pattern as R01/H02/R02/R03/UX01: a normal synchronous pytest test defines an inner async `exercise()` and runs it with `asyncio.run(exercise())`. This removes any dependency on pytest-asyncio marker registration while preserving the exact Textual `App.run_test()`/Pilot assertions.

Post-correction construction fixed point: **6 gate + 57 retained PRES + 94 cross-surface + 7 real Textual + 27 generated/release = 191 passed**, plus the fresh-process no-TERM final proof. Native Termux remains authoritative.
