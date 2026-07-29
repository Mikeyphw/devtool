# Phase 47B Android HUD Javac Compatibility Hotfix

Fixes the real-device Android HUD `:app:compileDebugJavaWithJavac` failures found after the Phase 47 regression seal.

## Fixes

- Adds `RepositoryPathResolver.resolve(RepositoryRecord)` as a compatibility alias for existing Debug Workbench and Guided Fix call sites.
- Updates `RepositoryStore.applyPathRepair(...)` to call `DevtoolDatabase.addRepositoryEvent(...)` with the required timestamp argument.
- Carries forward the Phase 47A `RepositoryDoctorResult.plainText()` escaped newline fix.

## Scope

This is a compile hotfix only. It does not change repo behavior, Git actions, support-bundle format, notification behavior, or repair safety semantics.
