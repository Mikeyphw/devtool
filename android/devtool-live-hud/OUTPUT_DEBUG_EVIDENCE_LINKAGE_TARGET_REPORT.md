# Android HUD Output Debug Evidence Linkage Target Report

This target-scoped hotfix records the Android HUD side of the output-debug evidence fix. The actual evidence writer is shared Devtool core code, so the companion `devtool` target hotfix must be applied first.

## Android target impact

- Android HUD validation and apply runs using `--output-debug` or `DEVTOOL_OUTPUT_DEBUG=1` inherit the new self-contained evidence behavior.
- Referenced run logs are preserved below `evidence/<run_id>/logs/` and linked from terminal-summary and clipboard metadata.
- The Android target seal remains target-specific through the existing `android_hud` target seal task set.

## Validation expectation

Apply after the core Devtool target hotfix and run the Android HUD target validation normally. The target-specific test ensures the report is present and the Android target still declares its seal task set.
