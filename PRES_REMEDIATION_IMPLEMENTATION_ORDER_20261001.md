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
63/76 PRES-R03   shortcut/CLI parity + broad real-app integration qualification
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

Current runtime status: DIST-R01 has passed and is applied. PRES-R01 v2, PRES-H01, and PRES-H02 v3 have passed native qualification and are applied. PRES-R02 v1 did not qualify: its native Pilot exposed a confirmation-flow/focus ownership defect and the transaction rolled back cleanly. PRES-R02 v2 is the same canonical 62/76 item, rebased onto qualified H02 v3 with visible-page focus transfer and real action-button confirmation coverage. General normal-execution live Textual remains open for the later PRES remediation boundary; no new roadmap position is consumed.

## Next-three merge-window decision

The next three PRES remediation items are intentionally **not merged**:

1. **PRES-R01** owns runner/progress presentation ownership only. Trackers become
   semantic/event producers and normal interactive execution becomes a Textual
   consumer, with plain/machine-safe fallback.
2. **PRES-R02** owns the concrete `devtool tui` widget/page/live-authority/action
   implementation. It is a different application/composition boundary.
3. **PRES-R03** owns shortcut/CLI argument parity, compatibility/responsive behavior,
   and broad concrete Textual `App.run_test()`/Pilot qualification.

`PRES-G2R` remains a separate fourth item in the gate window and audits all
R01..R03 work plus the reopened PRES-X04..X08/G1/G2 claims. `PRES-G3` remains the
later final seal after DIST, TEST, and EXEC are finally qualified.
