# PRES-UX01 Textual workspace revamp — revision 5

## Boundary

PRES-UX01 is a post-PRES-G2R presentation remediation that converges Devtool's three concrete Textual surfaces without creating new execution or artifact-lifecycle authority:

- normal command execution;
- the standalone `devtool tui` control center; and
- interactive artifact application, including apply launched from the control center.

Revision 3 preserves the revision-2 mobile screenshot requirements and rebases the same implementation boundary onto the exact post-TEST-G3 repository state. Revision 2 was never applied: pre-apply run `65f1321911fc48afabdafb73575ac944` failed source qualification because `.devtool.toml` had advanced through DIST-G3 and TEST-G3; the three touched Textual runtime source hashes still matched exactly.

Revision 3 then reached native validation in run `133a137ff8bd49ddb37d5dfb0b21cd47` but rolled back cleanly when Textual 8.2.8 rejected the CSS property `border-radius: 0`. The H02 source-start and R03 page-switcher failures in that run were downstream consequences of stylesheet parsing aborting before the real apps mounted. Revision 4 kept the same **68/77** ownership boundary and removed the invalid CSS, but native run `2f219c2e83ce4da3a1170c8c75ed3b5c` then exposed three real interaction regressions after the apps mounted: H02 replaced its last semantic phase with a generic completion string; R02 contextual actions hid selected-run controls on Diagnostics and the two-row short action strip clipped Textual's three-row buttons; and UX01 overlay selection did not immediately recompute the Apply button's enabled state. The run rolled back cleanly. Revision 5 fixes those interaction/state transitions at the same boundary. No execution/lifecycle authority changed.

Revision 2 originally folded in mobile screenshot evidence captured **before PRES-UX01 was applied**. The screenshots showed the old artifact host clipping metadata, leaving large dead vertical space after keyboard/tmux resize, pinning canonical qualification at `0%` while pytest batches were visibly running, and giving repetitive raw validator output more visual authority than the actual lifecycle state. Those observations are requirements of PRES-UX01 itself; they do not create a separate UX02.

The canonical RUN/EXO/LIFE/validation/transaction implementations remain authoritative. Textual only projects state, summarizes already-emitted child output, and forwards explicit user controls.

## Delivered UX

1. **Shared workspace language** — common responsive spacing, context headers, status labels, width breakpoints and new height breakpoints live in `devtool/textual_ux.py`.
2. **Phone/keyboard geometry** — concrete surfaces classify both width and height (`compact`, `ultra-compact`, `short`, `tiny`), refresh layout on terminal resize, hide nonessential chrome at the tightest sizes, and keep the log as the `1fr` consumer of all remaining height.
3. **Normal execution workspace** — bounds long command/stage text, hides the percentage bar when no denominator exists, reduces chrome on short terminals, and preserves a full-height live log.
4. **Artifact review-to-running transition** — the six-row artifact contract is a review-state surface. Once the child begins, it collapses automatically into a one-line artifact/target/change summary. `d` toggles full details when the viewport is large enough.
5. **Semantic artifact progress** — canonical progress wins when known. During a validator child, already-visible pytest `[NN%]` output can be projected explicitly as **validation-child progress**. If neither canonical nor child progress is known, the bar is hidden and the state is `working`; Devtool no longer presents absence of a denominator as a fake `0%` completion claim.
6. **Activity-first logs with lossless raw toggle** — repetitive pytest dot rows, separators and heartbeat spam are suppressed from the default activity view while still being retained in a bounded raw buffer. `r` rebuilds the log from that captured raw output; nothing is discarded merely because it was not initially surfaced.
7. **Useful heartbeat treatment** — heartbeat text updates the current-operation/activity summary rather than dominating the scrolling log. Test summaries and meaningful failure/transaction lines remain surfaced.
8. **Artifact stage rail** — wide terminals retain `Review → Qualify → Apply → Validate → Commit → Finish`; compact terminals use a bounded current-stage form such as `Validate 4/6 · next Commit → Finish` so long rails do not clip into adjacent widgets.
9. **Inline apply from the control center** — `devtool tui` reuses the H02 managed-child source in a full-screen Textual workspace with the same compact/detail/raw-log/progress rules and returns to Overlays after terminal completion.
10. **Contextual control-center actions** — the control center exposes only actions relevant to the current page. Short/tiny terminal classes reduce page chrome, status rows and details before sacrificing the table/log body.
11. **Dashboard glanceability** — a bounded summary line projects run activity/attention, validation state, and worker health above the existing tables.

## Screenshot-derived defects explicitly closed

- metadata is no longer intended to remain as a six-line pinned card while validation runs;
- long artifact names, phases and execution details are bounded before rendering;
- the apply/execution roots and log widgets use `height: 1fr`/`min-height` contracts so available vertical space is consumed by useful content rather than left blank;
- resize handling updates both width and height classes and forces a Textual layout refresh;
- very short viewports suppress optional headers/footers through the shared `tiny` class;
- validation can show `pytest 73%` (for example) as a child scope instead of displaying `0%` artifact completion;
- raw heartbeat/progress noise is recoverable with `r` but does not have to be the default primary view;
- mobile toolbars shed buttons in short mode while the keyboard bindings remain active.

## Safety / authority invariants

- `ArtifactApplyHostSource` remains the Textual-side apply bridge and still launches the ordinary `python -m devtool ... apply-overlay` lifecycle.
- `DEVTOOL_EXECUTION_PRESENTATION=plain` remains set in the managed apply child so nested execution does not fight the parent for the terminal.
- The artifact stage rail, pytest percentage parser and activity tracker are presentation-only. They cannot skip, approve, fail, commit, roll back or finalize lifecycle work.
- Canonical progress always outranks parsed output when it has a meaningful denominator. Parsed pytest percentage is labelled as validation-child progress, never silently promoted to overall artifact completion.
- Cancellation is forwarded to the canonical child/process-group path; Textual does not synthesize rollback or finalization truth.
- Machine JSON, quiet, redirected, verbose/trace, and explicitly plain paths retain their existing non-Textual behavior.

## Validation

`./devtoolw pres-ux01` owns three bounded layers:

1. shared workspace, responsive geometry, semantic-progress/activity contracts;
2. retained PRES-R01/R02/R03/H02/G2-remediation regression coverage; and
3. a **mandatory native Textual** `App.run_test`/Pilot matrix covering execution, artifact apply, control center, R03 interaction behavior, inline apply, and the revision-2 mobile resize proof.

The new concrete apply test starts at **48×14**, resizes through **52×20**, then returns to **92×30**. It checks compact/short/tiny class transitions, collapsed review state, semantic `pytest 73%` projection, nonzero log height, log containment within the screen region, details restoration after the viewport grows, and raw-log toggling. This specifically models the tmux + Android keyboard constraint seen in the supplied screenshots instead of testing only a comfortable desktop terminal.

The construction environment does not have Textual installed and has no network access, so native Textual qualification is intentionally not claimed here. The overlay validator requires that final native layer and fails closed where Textual is unavailable or the concrete matrix fails.

## Campaign placement

This remains **PRES-UX01**, gate-window overlay **1 of 1**, now explicitly placed at **68/77**. Revision 1 and revision 2 were never applied. Revision 3 applied only inside a transaction, failed native stylesheet qualification, and rolled back cleanly; revision 4 also failed native interaction qualification and rolled back cleanly; revision 5 supersedes it at the same source-rebased 68/77 boundary.

The active denominator moves from 76 to **77** because PRES-UX01 is inserted after qualified TEST-G3 and before EXEC-G3. Already-applied evidence retains its original run/commit facts; active-plan reporting uses the refreshed denominator. On successful native qualification, the next overlay is **EXEC-G3 69/77**; **PRES-G3 70/77** remains the later cross-surface presentation seal, followed by the RMP tail at **71/77..77/77**.

The next-three merge-window decision is **no merge**: PRES-UX01 is implementation at the concrete Textual workspace boundary, while EXEC-G3 and PRES-G3 are independent authoritative seals.


## Revision 5 native-interaction remediation

Native run `2f219c2e83ce4da3a1170c8c75ed3b5c` reached the mandatory real Textual matrix and failed 3 of 6 concrete proofs, with 0 warnings and 1 error. Rollback restored 7 modified paths, removed 4 newly added paths, restored Git state, and verified the transaction. The failures were not authority failures; they were renderer interaction defects introduced by UX01.

Revision 5 makes four concrete corrections:

1. the standalone apply host remembers its last semantic phase (for example `artifact validation`) and preserves that phase after the canonical child completes instead of replacing it with `Devtool completed successfully`;
2. Logs and Diagnostics remain selected-run inspection surfaces and therefore retain canonical run actions while the operator investigates output/failures;
3. `DataTable.RowHighlighted` immediately recomputes selection-derived action enabled-state, so selecting a ready overlay enables `Apply overlay` without waiting for a provider refresh tick; and
4. the short control-center action bar is a full three rows high, matching Textual 8.2.8's default button height so 64×22 and 52×20 controls remain visible and clickable.

The user supplied a Textual wheel bundle after the v4 failure. Using Textual **8.2.8** and Rich **15.0.0**, the v4 failures were reproduced locally before changing code. After the v5 correction the complete mandatory real-App slice passes locally: **6 passed, 49 deselected**. The two non-real validation layers also pass (**9 workspace contracts** and **63 retained PRES contracts**). The supplied bundle resolved a few ancillary dependency wheels to versions different from the tablet freeze, so this is high-fidelity reproduction rather than a claim of byte-identical environment; native Termux qualification remains the final authority.
