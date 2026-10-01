# PRES-R02 — Concrete Textual control-center application + live canonical truth/actions

**Position:** gate-window overlay 2 of 4; implementation 2 of 3; full-plan 62/76.

## Merge-window decision

The next-three PRES remediation window remains intentionally unmerged. PRES-R01 owns normal execution presentation; PRES-R02 owns the concrete `devtool tui` application and live canonical projection/control boundary; PRES-R03 owns shortcut/CLI parity and broad real-app qualification. PRES-G2R remains a separate gate.

## Runtime truth repaired

The historical Textual shell was `Header + Static + Footer`, changed only `control_center.page`, and appended `Last control: {...}` debug dictionaries to the visible body. PRES-R02 replaces that runtime with a real Textual application.

Concrete pages now exist for Dashboard, Runs, Logs, Diagnostics, History, Targets, Workers, Validation, Artifacts, Overlays and Evidence. They are switched through `ContentSwitcher`; run/history/diagnostic/target/worker/validation/artifact/overlay/evidence collections use `DataTable`; run nodes use `Tree`; logs use bounded `RichLog`; confirmations/help use `ModalScreen`.

## Canonical state ownership

`CanonicalControlCenterProvider` is a read/projection boundary over existing Devtool authorities: IPC/execution store, CORE RUN catalog, durable validation sessions, LIFE artifact operation history, DIST/SSH provider evidence, Devtool logstore and preserved evidence root. It does not create a durable TUI database and explicitly reports `owns_truth=false`.

The app refreshes that provider on a Textual interval. Selection is frontend state only and is preserved across refreshes; canonical execution/validation/artifact truth remains external.

## Real controls

Run cancel/retry/rerun/resume dispatch through the existing IPC broker. Validation pause/stop writes canonical durable validation control requests. Active transaction rollback/recovery delegates to the transaction engine. Consequential actions require Textual confirmation modals. No model `confirmed=True` shortcut is used as the operator interaction.

Artifact/overlay apply and worker-recovery compatibility are intentionally not marked complete here because their CLI/argument compatibility belongs to PRES-R03.

## Logs and diagnostics

Logs render actual bounded content, not `Log lines: N`; reads are bounded by bytes and lines and refresh live. The page now has explicit follow/pause control, a live text filter, selected run/node context, and a `Copy visible` action through Devtool’s existing clipboard authority. Diagnostics expose canonical severity, summary, what happened, why/failure kind, location, remediation, evidence, source, related run/node and log path when present.

## Responsive/runtime behavior

The interactive app no longer supplies fake `100x28` dimensions. Textual owns current terminal dimensions; resize events switch narrow layouts by hiding the navigation rail while keeping page/selection state. The global action strip is now kept outside the 1fr ContentSwitcher budget and uses Textual HorizontalScroll, so narrow terminals can reach every consequential control without pushing the strip below the viewport. Broader narrow-Termux interaction qualification remains deliberately in PRES-R03.

## Targeted validation

Construction validation: 180 retained PRES X01..X08/G1/G2/R01/R02/H02 tests passed with the three real-Textual integration tests intentionally deselected on the construction host. The strengthened R02 non-Textual contract boundary is 8/8 passed, including canonical action delegation and roadmap/workflow/ledger enforcement. The native `pres-r02` workflow sets `DEVTOOL_PRES_R02_REQUIRE_TEXTUAL=1`, making the real `App.run_test()`/Pilot control-center proof mandatory on Termux.

Broad reopened historical qualification is intentionally deferred to PRES-G2R; final cross-surface qualification remains PRES-G3.

## Promise ledger

See `PRES_R02_PROMISE_LEDGER.json`.

## Next

`PRES-R03 — 63/76`. One implementation overlay remains before `PRES-G2R — 64/76`. DIST-R01 is already qualified/applied; after the PRES remediation gate, the next DIST item is DIST-G3.


## Native v1 failure and v2 correction

PRES-R02 v1 did not qualify on native Termux. The real Pilot test reached the destructive action boundary but the expected modal was not the active screen after the keyboard path. v2 does not weaken confirmation: it makes page navigation transfer focus into the newly visible page, then validates the consequential action through the visible Cancel control and real `ConfirmationModal`. Keyboard shortcut unification/collision policy remains explicitly owned by PRES-R03. v2 is rebased on qualified/applied PRES-H02 v3 so artifact application retains the legal main-thread Textual host.


## Native v2 failure and v3 correction

PRES-R02 v2 reached the real Textual app on native Termux but `Pilot.click("#action-cancel")` failed with `OutOfBounds`: the global action strip could be laid out below the visible region because the ContentSwitcher had no explicit 1fr budget. v3 fixes the runtime layout rather than weakening the test: `#page-switcher` is explicitly bounded to the remaining vertical space and the action region is a fixed-height `HorizontalScroll`, preserving mouse/focus reachability even on narrow Termux terminals. The real Pilot test still performs the direct wide-screen click and additionally resizes to 64x22, scrolls the action into view, and exercises the confirmation modal again.
