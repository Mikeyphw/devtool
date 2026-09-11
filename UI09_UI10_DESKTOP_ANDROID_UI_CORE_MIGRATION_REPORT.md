# UI09 + UI10 implementation report

## Scope delivered

UI09 migrates GTK Control Center and GTK HUD to the shared semantic UI contract. A desktop renderer adapter now normalizes operation, presentation, responsive/capability state, navigation, actions, progress, diagnostics and notifications. The Control Center publishes this as `desktop_ui`; GTK HUD consumes the same semantics for title/detail/progress/diagnostics/action availability while preserving legacy state fallback.

UI10 migrates the Android Live HUD bridge and native companion. Python forwards independently versioned UI Core sections, `UiSemanticState` parses them natively, `HudUpdate` prefers semantic operation/progress data, notifications prefer semantic action IDs, the overlay HUD renders semantic diagnostics and progress-tree steps, and `RunStore` persists semantic status/phase/detail/progress/steps so reopened run history does not fall back to legacy live fields.

## Compatibility

- Android bridge protocol remains v1; semantic extras are additive.
- Existing bridge actions UPDATE/COMPLETE/FAIL/CANCEL/TOAST are unchanged.
- Existing Tasker, IPC and HUD fields remain present.
- GTK routes and existing desktop preferences remain valid.
- Old state files and old Android senders continue through legacy fallbacks.

## Validation contract

Dedicated UI09/UI10 contracts cover desktop normalization, Android semantic payload generation, native parser/action/diagnostic integration, semantic run-history persistence and additive protocol compatibility. The final deterministic gate is split into two non-overlapping selections: 535 core/runtime tests + 8 subtests and 326 UI/Desktop/Android tests + 37 subtests, for 861 tests + 45 subtests total with zero failures.

Android Gradle execution could not run in the packaging sandbox because the Gradle bootstrap distribution was not locally cached and outbound network is disabled; source-level Android integration contracts are therefore included in the Python gate, while the user's Termux/Devtool validation environment remains the authoritative Android toolchain gate when applicable.
