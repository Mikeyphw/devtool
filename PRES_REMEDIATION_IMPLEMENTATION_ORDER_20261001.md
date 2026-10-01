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
separate `DIST-R01` remediation before DIST-G3. The active plan therefore has
**76 positions**.

## Refreshed active tail

Positions through qualified LIFE-G3 remain immutable historical evidence through
`60/70`. New active reporting starts here:

```text
61/76 PRES-R01   normal execution presentation ownership
62/76 PRES-R02   concrete Textual control-center application + live truth/actions
63/76 PRES-R03   shortcut/CLI parity + normal execution main-thread host + broad real-app qualification
64/76 PRES-G2R   reopened concrete-runtime requalification gate
65/76 DIST-R01   known DIST-P100 candidate-runtime self-refresh remediation
66/76 DIST-G3    final distributed-execution seal
67/76 TEST-G3    final test-system seal
68/76 EXEC-G3    final execution/reuse/validation seal
69/76 PRES-G3    final cross-surface presentation seal
70/76 RMP-X01
71/76 RMP-X02
72/76 RMP-G1
73/76 RMP-X03
74/76 RMP-G2
75/76 RMP-X04
76/76 RMP-G3 / GRAND DEVTOOL
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
