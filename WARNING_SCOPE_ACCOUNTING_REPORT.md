# Warning Scope Accounting Fix

## Problem

Artifact validation contracts previously counted every parsed warning as a target warning. Environment notices emitted by Devtool/Gradle (for example low free-space notices, ADB probe failures, unsupported/native-platform notices, and the custom-AAPT2 infrastructure warning) could therefore change an otherwise successful target phase from `passed` to `warning` and violate `maximum_warnings = 0`.

## Fix

Devtool now has an explicit target-warning budget. Infrastructure warnings remain structured `warning` diagnostics and stay visible in logs, summaries, the HUD, JSON, and support evidence, but they do not participate in artifact target-outcome matching or `validation.maximum_warnings`.

Excluded infrastructure codes are currently:

- `LOW_STORAGE`
- `ADB_PROBE`
- `GRADLE_ENVIRONMENT`
- `GRADLE_INFRA`
- `GRADLE_METASPACE_PRESSURE`

Real target warnings such as Kotlin/Java/compiler warnings, Android lint warnings, pytest warnings, Gradle deprecations from the project, and generic target warnings still count.

The validation-contract metadata now records both `validation_target_warning_count` and `validation_infrastructure_warning_count`, and warning-limit evidence states that the limit scope is `target`.
