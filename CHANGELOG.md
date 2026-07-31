# Changelog

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
