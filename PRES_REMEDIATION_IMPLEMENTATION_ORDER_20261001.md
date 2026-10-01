# PRES/Textual remediation implementation-order refresh — 2026-10-01

This amendment extends the existing canonical PRES campaign. It does not replace,
renumber, or delete historical PRES-X01..X08, PRES-G1, PRES-G2, or LIFE-G3 evidence.

## Why the active denominator changes

The handoff is internally inconsistent at the old tail: LIFE-G3 is immutable user
evidence at `60/70`, while the older canonical list placed it at 59 and also kept
EXEC-G3 plus every RMP gate separate. The active refresh must preserve historical
labels rather than rewrite them. That existing live-history offset contributes one
position.

The newly reopened PRES runtime work adds three implementation overlays and one
separate requalification gate. A previously discovered DIST-P100 gap also adds the
separate `DIST-R01` remediation before DIST-G3. The active plan therefore had **76 positions** before the later PRES-UX01 insertion.

After TEST-G3 qualified, the previously prepared PRES-UX01 mobile Textual workspace revamp was rebased onto the current repository and inserted as a real implementation boundary before EXEC-G3. That adds one position without rewriting already-applied historical evidence, so the active plan now has
**77 positions**.

## Refreshed active tail

Positions through qualified LIFE-G3 remain immutable historical evidence through
`60/70`. New active reporting starts here:

```text
61/77 PRES-R01   normal execution presentation ownership
62/77 PRES-R02   concrete Textual control-center application + live truth/actions
63/77 PRES-R03   shortcut/CLI parity + normal execution main-thread host + broad real-app qualification
64/77 PRES-G2R   reopened concrete-runtime requalification gate
65/77 DIST-R01   known DIST-P100 candidate-runtime self-refresh remediation
66/77 DIST-G3    final distributed-execution seal
67/77 TEST-G3    final test-system seal
68/77 PRES-UX01  mobile-responsive Textual workspace + inline artifact-apply UX convergence
69/77 EXEC-G3    final execution/reuse/validation seal
70/77 PRES-G3    final cross-surface presentation seal
71/77 RMP-X01
72/77 RMP-X02
73/77 RMP-G1
74/77 RMP-X03
75/77 RMP-G2
76/77 RMP-X04
77/77 RMP-G3 / GRAND DEVTOOL
```

`DIST-R01` is recorded here only for full-plan arithmetic and ordering. It remains
DIST-owned and is not part of the PRES remediation gate window.

Current runtime status: DIST-R01 has passed and is applied. PRES-R01 v2, PRES-H01, PRES-H02 v3, and PRES-R02 v3 have passed native qualification and are applied. PRES-R02 v1/v2 native failures were folded into the same canonical 62/76 item; v3 closed both the modal/focus and narrow action-strip viewport defects. PRES-R03 is now the final implementation boundary before PRES-G2R and closes the reopened general normal-execution live Textual host plus shortcut/CLI/concrete-app parity. R03 v1 native run `381b4e609f324c408ab30287ea8e8e9a` rolled back cleanly on stale packaged action-parity assertions and exposed a Textual-invalid H02 confirmation stylesheet expression; R03 v2 folded both corrections into the same 63/76 boundary. Native v2 run `d976675c392d4a62ac4eada61506250f` then passed three real Textual cases and rolled back on the fourth solely because the new qualification test imported `ContentSwitcher` from `textual.containers`; the runtime and retained R02 tests use `textual.widgets.ContentSwitcher`. R03 v3 corrected that qualification-only compatibility mismatch. Native v3 run `881acebe9965432aa5420b05f530f97f` passed every non-Textual/retained stage and 3/4 real Textual cases, then exposed a horizontally off-screen overlay-action Pilot click and a genuine refresh-timer teardown race (`NoMatches` after the default Screen detached). R03 v4 reuses the retained R02 `scroll_visible()` mouse-proof pattern and the qualified H02 timer-stop/`NoMatches`/`on_unmount` lifecycle pattern in the same 63/76 boundary. No hotfix consumes a new roadmap position.

## Next-three merge-window decision

The next three PRES remediation items are intentionally **not merged**:

1. **PRES-R01** owns runner/progress presentation ownership only. Trackers become
   semantic/event producers and normal interactive execution becomes a Textual
   consumer, with plain/machine-safe fallback.
2. **PRES-R02** owns the concrete `devtool tui` widget/page/live-authority/action
   implementation. It is a different application/composition boundary.
3. **PRES-R03** owns shortcut/CLI argument parity, compatibility/responsive behavior,
   the legal main-thread Textual host for ordinary execution, and broad concrete
   Textual `App.run_test()`/Pilot qualification.

`PRES-G2R` remains a separate fourth item in the gate window and audits all
R01..R03 work plus the reopened PRES-X04..X08/G1/G2 claims. `PRES-G3` remains the
later final seal after DIST, TEST, and EXEC are finally qualified.

- PRES-R03 v5 retry remains full-plan 63/76: native v4 exposed queued Textual row-highlight teardown after the default Screen detached; v5 adds a projection-wide lifecycle barrier. PRES-G2R remains next at 64/76. Native v5 then exposed Textual 8.2.8 built-in Ctrl+P command-palette precedence; v6 disables the framework palette so the canonical Devtool CommandPaletteModal owns Ctrl+P. PRES-G2R remains next at 64/76.

- PRES-R03 v6 qualified/applied on native Termux: run `9b5fff37ecd14b9c81d294a00540183b`, **175 passed, 0 failed, 0 skipped**, zero diagnostics, commit `0fd7d0c`. The active item is now the separate **PRES-G2R 64/76** gate. After a successful G2R, skip already-qualified/applied DIST-R01 65/76 and proceed to **DIST-G3 66/76**.

- PRES-G2R qualified/applied on native Termux: run `c96acfc4493f40fc818f7086e6a68611`, **302 passed, 0 failed, 0 skipped**, zero diagnostics, commit `3ffbf98`. DIST-R01 65/76 was already complete, so the active item is now **DIST-G3 66/76**.

- DIST-G3 qualified/applied on native Termux: run `3f724127f02d4e538d7f526b2e76a950`, **423 passed, 0 failed, 0 skipped**, zero diagnostics, commit `572b94d`. DIST finalization is closed; the active item is now **TEST-G3 67/76**.


## 2026-10-01 post-TEST-G3 PRES-UX01 insertion

- TEST-G3 qualified/applied on native Termux: run `2ba456d3f6024b3eaa65885646eefbbf`, **246 passed, 0 failed, 4 skipped**, zero diagnostics, commit `4269091`.
- The previously prepared `pres_ux01_textual_workspace_revamp_v2.zip` was based on PRES-G2R commit `3ffbf98`. It was **not applied**: pre-apply source qualification run `65f1321911fc48afabdafb73575ac944` failed only because `.devtool.toml` had legitimately advanced through DIST-G3 and TEST-G3. The three touched Textual runtime sources still matched their declared source hashes exactly.
- PRES-UX01 is therefore inserted now as **68/77**, rebased onto the exact post-TEST-G3 tree. It remains a one-overlay presentation remediation with targeted native Textual validation.
- Next-three merge-window decision after this insertion: **PRES-UX01 → EXEC-G3 → PRES-G3 are not merged**. PRES-UX01 is concrete presentation implementation; EXEC-G3 and PRES-G3 are separate authoritative seals over different ownership boundaries.
- On successful PRES-UX01 qualification, next is **EXEC-G3 69/77**. After EXEC-G3, **PRES-G3 70/77** remains the cross-surface presentation seal; the RMP tail then occupies **71/77..77/77**.
### PRES-UX01 revision-5 native interaction correction

- Native run `133a137ff8bd49ddb37d5dfb0b21cd47` applied PRES-UX01 revision 3 transactionally, then failed required validation because Textual 8.2.8 rejected `border-radius: 0` in the new workspace stylesheet. The transaction rolled back, so campaign source state returned to the post-TEST-G3 base.
- Revision 4 removed the unsupported CSS and reached the real-App matrix in native run `2f219c2e83ce4da3a1170c8c75ed3b5c`, where 3 concrete interaction proofs failed and the transaction rolled back cleanly. The failures were reproduced under Textual 8.2.8 locally: H02 overwrote its last semantic phase on completion; Diagnostics hid selected-run controls; selected overlay rows did not immediately refresh action enabled-state; and the 2-row short action strip clipped Textual's 3-row buttons.
- This remains remediation inside **PRES-UX01 68/77**, not a new roadmap position. Revision 5 preserves semantic phase context, keeps run controls on run-inspection pages, refreshes selection-derived action state immediately, and restores a full 3-row clickable short action strip. The six mandatory real Textual App/Pilot tests pass locally under Textual 8.2.8; native Termux remains authoritative.
- On successful revision-5 qualification the next item remains **EXEC-G3 69/77**, followed by **PRES-G3 70/77** and RMP **71/77..77/77**.


## 2026-10-01 PRES-UX01 native closure and EXEC-G3 activation

- PRES-UX01 revision 5 qualified/applied on native Termux: run `c2a81967b15d4c56a6044e3620cc1e68`, **157 passed, 0 failed, 0 skipped**, zero diagnostics, commit `35ea55c`. PRES-UX01 is closed at **68/77**.
- The next-three merge window is **EXEC-G3 → PRES-G3 → RMP-X01**. No merge is permitted: EXEC-G3 and PRES-G3 are independent authoritative final seals, while RMP-X01 begins the final roadmap-policy scope.
- The active item is **EXEC-G3 69/77**, owning the final execution/reuse/validation seal and the observed nonterminal-descendant finalization incident (`xdm_modern.validate.$runner`, `xgo_final_wave12.validate.wave12_final_gate`).
- On successful EXEC-G3 qualification, next is **PRES-G3 70/77**, followed by RMP **71/77..77/77**.

## 2026-10-01 EXEC-G3 native v1 qualification retry

- EXEC-G3 v1 failed and rolled back cleanly on native Termux: run `060a039b99fc401a97171607bcaa6111`; duration 212.85s; zero warnings; one validation error. Runtime incident closure was not implicated.
- Root cause: retained EXO-P0 tests launch nested Devtool CLI runs and did not isolate the validator's execution-routing context. Under native artifact validation the nested process could resolve the diagnostic-only substrate `host-android`, which is intentionally not in the canonical execution-environment registry.
- EXEC-G3 v2 remains the same **69/77** boundary. It clears inherited `DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT` and `DEVTOOL_EXECUTION_ENVIRONMENT` for the nested probes and explicitly binds them to a supported parent substrate, using the Termux Python executable path when HOME/PREFIX markers were sanitized. No alias for `host-android` is introduced.
- Construction fixed point after remediation: gate 6, retained foundation 58, retained validation 13, operational adoption 29, lifecycle closure 25, interruption continuation 7, generated/release 27; **165 pytest tests passed** plus the fresh-process EXEC-G3 final proof.
- Next remains **PRES-G3 70/77** only after native EXEC-G3 v2 qualification.


## 2026-10-01 EXEC-G3 native v2 qualification retry

- EXEC-G3 v2 failed and rolled back cleanly on native Termux: run `c4ad9ed376324718a55e8ad6112de6bf`; duration 208.28s; zero warnings; one validation error. The runtime incident closure was not implicated.
- v2 removed inherited routing variables correctly but still forced the nested generic EXO-P0 workflow to `native-termux`. Artifact validation had sanitized Termux HOME/PREFIX markers, so canonical host detection observed the Android host as `host-android`; native-Termux preflight correctly rejected the mismatch (`termux: host-android`).
- EXEC-G3 v3 remains the same **69/77** boundary. Generic nested CLI finalization probes now clear inherited routing overrides and use the public `--execution-environment host` selector, which resolves to the substrate the nested process actually observes. No `host-android` alias is added to the canonical execution-environment registry.
- A dedicated construction regression reproduces the native sanitization case by forcing Android host detection while removing Termux markers; the three-node nested workflow succeeds and seals under the `host` selector.
- Construction fixed point after v3 remediation: gate 6, retained foundation 58, retained validation 13, operational adoption 29, lifecycle closure 26, interruption continuation 7, generated/release 27; **166 pytest tests passed** plus the fresh-process EXEC-G3 final proof.
- Next remains **PRES-G3 70/77** only after native EXEC-G3 v3 qualification.
