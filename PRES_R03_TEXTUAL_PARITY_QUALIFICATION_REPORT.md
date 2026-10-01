# PRES-R03 — Textual parity and concrete-runtime qualification

**Position:** gate-window overlay 3 of 4; implementation 3 of 3; full-plan 63/76.

## Merge-window decision

The next-three R01/R02/R03 remediation window remains deliberately unmerged. R01 owns execution presentation separation, R02 owns the concrete control-center widget/runtime projection, and R03 owns shortcut/CLI parity, the general main-thread Textual execution host, and broad concrete-app qualification. **PRES-G2R stays separate as the authoritative gate.**

## Delivered

- One canonical shortcut/page authority feeds Textual, UI Core and the fenced legacy TUI; collisions are audited globally and Workers is discoverable.
- `devtool tui --page` and the standalone runtime accept all concrete pages plus explicit compatibility aliases. `build` migrates to `runs` with a notice.
- `--artifact` is no longer ignored: canonical overlay preflight is projected on the Overlays page and in renderer-independent `--once`; auto/Textual `--once` stays on the canonical projection even when the optional Textual package is unavailable rather than silently dropping to legacy. Interactive `--width` is explicitly treated as a `--once`/legacy override while Textual follows live terminal dimensions.
- The help surface is a concrete shortcut table and Ctrl+P is a real command palette.
- Ordinary interactive execution now uses a legal main-thread Textual host. The parent owns terminal presentation; a managed child remains the only execution authority and publishes existing Tasker/RUN state. Artifact apply continues to use the specialized H02 host.
- Machine, quiet, verbose/trace, CI, redirected, explicit plain and stdout-payload paths remain plain/machine-safe.
- Safe mutation parity is concrete: overlay apply and stale SSH worker recovery use real confirmation modals and delegate to canonical Devtool CLI/domain authorities. Generic artifact apply, target launch, and worker cancel/resume remain explicitly unadvertised rather than retained as fake metadata.
- Broad actual `App.run_test()`/Pilot qualification is mandatory on the native Textual host, covers confirmed overlay/worker operations plus navigation/focus/log/diagnostic/resize/failure transitions, and re-runs retained R01/R02/H02 concrete app proofs.

## Native rollback corrections

Native-Termux run `381b4e609f324c408ab30287ea8e8e9a` rolled v1 back cleanly. The first failure was packaging drift: the ZIP carried an older parity assertion that still classified concrete `overlay.apply` and stale-worker `worker.recover` controls as unsupported even though the R03 runtime and refreshed handoff ledger already delegated both to canonical authorities. The retained PRES-G2 remediation assertion had the same stale expectation.

The same run also surfaced a pre-existing PRES-H02 stylesheet parser defect in `ApplyConfirmationModal`: Textual rejects CSS `width: min(72, 92%)`. R03 v2 folds that concrete-runtime blocker into this owning qualification boundary as `width: 92%; max-width: 72`, adds a static regression for the parser-safe form, and extends the real Pilot matrix to prove confirmed overlay apply and worker recovery dispatch. No new roadmap position is consumed.

Native v2 run `d976675c392d4a62ac4eada61506250f` then reached the real Textual matrix: three cases passed and the remaining R03 case failed before app construction because the new test imported `ContentSwitcher` from `textual.containers`. The runtime itself and retained R02 real-App coverage already use the compatible API, `textual.widgets.ContentSwitcher`. R03 v3 fixes only that qualification import and retains the same implementation, source guards, and mandatory native real-App boundary.

Native v3 run `881acebe9965432aa5420b05f530f97f` passed the R03 parity contracts (11 passed), the retained PRES fixed point (68 passed), and three of four concrete Textual app cases. The remaining real-App case exposed two same-boundary defects: the overlay-apply button lives later in the horizontally scrollable global action strip, so Pilot must scroll it visible before a mouse click; and the periodic control-center refresh timer could fire once more while `run_test()`/`App.exit()` detached the default Screen, converting normal teardown into `NoMatches` on `#dashboard-runs`/`#status-line`. R03 v4 uses the already-passing R02 `scroll_visible()` pattern for the action and adopts the qualified H02 timer lifecycle pattern: store the interval timer, stop it on `NoMatches`, and stop it again in `on_unmount`.

## Qualification boundary

R03 targeted validation is implementation-owned. The authoritative reopening audit over PRES-X04..X08/G1/G2 and all remediation/hotfix work remains **PRES-G2R — 64/76**. PRES-G3 later seals the complete cross-surface presentation architecture and legacy HUD disposition.

## Next

**PRES-G2R — 64/76.** No implementation overlays remain before that gate.

### v5 native lifecycle correction

Native-Termux run `9bbd9be6985f43f5b11fc2fd35023c70` rolled v4 back cleanly after the real-App lane reached shutdown. The v4 timer guard was necessary but insufficient: `DataTable.clear()/add_row()` can queue `RowHighlighted` callbacks, and those callbacks invoke `_render_details()` outside `refresh_from_provider()`. A queued callback therefore queried `#dashboard-detail` after the default Screen had detached and bypassed the interval callback's `NoMatches` handler.

R03 v5 keeps the same 63/76 boundary and adds one projection-wide mounted/shutdown barrier. The control center marks the projection live on mount, detaches idempotently before quit and on unmount, stops the refresh timer at the same barrier, and gates queued input/list/table/tree events plus render helpers on that state. Detached-DOM `NoMatches` in detail/log/action/navigation rendering is treated as normal teardown rather than an application failure. No execution, run-store, validation, overlay, worker, or artifact authority moves into Textual.

### v6 canonical Ctrl+P ownership correction

Native-Termux run `3cf0cbb98ed744bfb9caeb80e3820007` rolled v5 back cleanly after the lifecycle-hardened real-App path reached command-palette qualification. Textual 8.2.8 enables its framework command palette by default and injects a priority `Ctrl+P -> command_palette` binding during App initialization. That priority binding preempted Devtool's canonical R03 `Ctrl+P -> show_command_palette`, so Pilot correctly observed the framework `CommandPalette` rather than Devtool's `CommandPaletteModal`.

R03 v6 keeps the same 63/76 implementation boundary and disables Textual's built-in palette on `DevtoolTextualControlCenter` with `ENABLE_COMMAND_PALETTE = False`. The canonical shortcut registry remains the only `Ctrl+P` owner, and the existing Devtool modal continues to derive its commands from Devtool presentation/action intent. Static construction and native real-App assertions now verify both disabled framework palette state and concrete Devtool modal activation. No domain authority moves into Textual.

