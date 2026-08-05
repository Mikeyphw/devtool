# Changelog

## Desktop launcher cleanup

- Removed the obsolete `devtool-gtk-control-center` and `devtool-gtk-hud` compatibility executables.
- Kept `devtool-control-center` and `devtool-control-center-hud` as the only standalone desktop commands.

## Desktop HUD and Control Center phase 6-7

- Replaced the Control Center Run page shell launch with a direct argv process launcher that assigns a stable desktop run id, captures live stdout/stderr, writes redacted per-run logs, and supports graceful cancel plus force-cancel through the process group.
- Added persisted desktop preferences for window size, selected Control Center page, appearance, and reduced motion.
- Added Adwaita appearance hooks, semantic accent colors, keyboard shortcuts, accessibility labels, and reduced-motion handling for the GTK Control Center and HUD.

## Chroot Gradle graph transport and completion fallback

- Preserved `DEVTOOL_RUN_ID` explicitly through the Termux-host `sudo/chrootctl` toolchain wrapper so structured Gradle graph events are accepted by the host progress tracker.
- Counted unique fallback `> Task` lines, parsed Gradle actionable-task summaries, and inferred a successful final total when structured events are unavailable.
- Prevented completed explicit-task runs from remaining at `N/? tasks • discovering dependencies`; final output now reports requested and dependency counts.


## Android Live HUD bridge variant protocol fix

- Derived bridge action namespaces from the resolved debug or release application ID instead of pairing a debug component with release actions.
- Added protocol versioning, ordered-broadcast acknowledgements, rejection parsing, and local unsupported-action guards.
- Made the bundled Termux helper consume pairing package/component markers and added executable debug/release protocol regression coverage.


## 1.1.0-rc.3 desktop GTK4 packaging flip

- Promoted GTK4 Control Center packaging and removed brittle historical PHASE_* Markdown release-test requirements.


## Desktop GTK4 migration phase 1-2

- Extracted reusable desktop command, HUD snapshot, IPC, history, repository, and artifact models under `devtool.desktop`.
- Wired the legacy Qt Run page advanced fields so environment assignments, extra global args, extra operation args, and the `--yes` checkbox affect generated commands.
- Added a GTK4/libadwaita compact HUD and made `desktop-window` prefer it, with `qt-window` retained as the explicit legacy Qt fallback and `gtk-window` kept as a deprecated GTK3 compatibility path.
- Added `devtool-gtk-hud` as a direct executable for the GTK4 HUD and preserved `--once` JSON snapshots without requiring a display.


## Interactive validation cockpit promise-completion fixes

- Added revision guards to every active-session continuation, retry, exact-phase, through-phase, and selected-phase command.
- Replaced cached Ctrl-R repainting with real catalog reloads and added an explicit refresh action to the numbered fallback.
- Resolved pre-session phase choices from the real project runner plan instead of a hardcoded Android/generic list.
- Added reset-from-phase, stable pretty/compact JSON output selection, generic supported safe-retry profiles, and combined multi-phase dependency previews.
- Preserved repository, target, variant, environment, HUD, clipboard, and output arguments in saved recipes.
- Added multi-setting context/resource pages, detected-context warnings, a deprecation warning for validation `--profile`, and pseudo-terminal fallback coverage.
- Fixed duplicate `--hud-overlay` generation.

## Interactive validation cockpit and wizard

- Added `devtool menu validation`, `devtool validation-menu`, and `devtool vm` as a state-aware fzf/bat validation cockpit with a complete numbered fallback.
- Added guided flows for one-shot, collect-all, durable sequential, watch, matrix, Wine, artifact/bundle integrity, and apply-time validation.
- Added revision-guarded next, through, selected, remaining, safe retry, pause, stop, recover, reset, plan acceptance, finalize, report, live-log, and keep-or-rollback abort actions.
- Added canonical command review with run, dry-run, clipboard copy, editor handoff, live logs, and global or repository TOML recipes.
- Added one-run validation resource overrides for workers, CPUs, Gradle heap, memory guard, priority, parallelism, build cache, and daemon policy. Explicit overrides take precedence over resource profiles and persist in durable session options.
- Added Gradle task discovery, dependency-closure previews, complete structured test selection, runtime/device guards, shell completion, manual documentation, and regression coverage.

## Phase 32 validation reports / Phase 33 release seal

- Retained immutable validation reports with stable hashes, original and final Git heads, and distinct validation-history cards.
- Retained finalization-integrity gates, the 16 mandatory failure scenarios, and the Android toolchain release gate.

## Validation finalization history-path hotfix

- Fixed automatic finalization failing with Git exit 128 when `.devtool/artifacts.json` is absent and untracked.
- Artifact history paths are now staged only when they exist or are already tracked, while tracked deletions remain supported.
- Added regression coverage for absent, existing, ignored, and deleted tracked artifact-history registries.

## 47C-5/47C-6/47C-7

- Completed backup rule metadata for legacy Auto Backup plus Android 12+ data extraction.
- Added monochrome launcher icon support.
- Fixed overlay drag header accessibility performClick contract.
- Moved remaining lint-reported setText concatenations into string resources/plurals.
- Removed dead color/style/drawable resources.

## UI contract archival cleanup

- Finalized the UI/UX contract gate without requiring historical PHASE_* Markdown files.
