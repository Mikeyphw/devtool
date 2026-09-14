# Gradle Task Graph Optimization v1

## Purpose

This overlay turns Devtool's Android/Gradle validation task handling from simple de-duplication into an opt-in task graph compaction path. It is designed for small Android targets such as `android_hud`, where the project already exposes a safe aggregate Gradle task that shares one task graph for assemble, unit tests, and lint.

## Delivered changes

- Added a config-aware Android Gradle task execution plan in `devtool/android_integration.py`.
- Preserved explicit `--task` semantics: `validation_phase="explicit-tasks"` does not coalesce configured aliases.
- Added opt-in `[targets.<name>.gradle.task_aliases.<alias>]` support.
- Changed the Android HUD seal task from three independent Gradle tasks to the existing `devtoolDebugValidation` aggregate.
- Updated validation metadata so `gradle_tasks` reflects the actual task names sent to the builder, while `requested_gradle_tasks`, `gradle_workload_tasks`, and `gradle_task_optimization` preserve the expanded evidence trail.
- Updated the target seal expectation so Android HUD's sealed task contract matches the optimized aggregate task.

## Android HUD optimization

Before this overlay, final validation commands for the Android HUD target commonly requested:

```text
:app:assembleDebug
:app:testDebugUnitTest
:app:lintDebug
```

The project already defines:

```text
devtoolDebugValidation -> :app:assembleDebug + :app:testDebugUnitTest + :app:lintDebug
```

After this overlay, normal validation can coalesce the three-task set into:

```text
devtoolDebugValidation
```

This lets Gradle build one shared graph, avoid duplicate launcher/configuration overhead, and still run the same underlying package/test/lint work.

## Safety rules

- Aliases are opt-in per target and are never guessed from task names.
- Aliases only apply when every configured source task is present.
- Explicit CLI `--task` validation is preserved after conservative component pruning and does not use configured aliases.
- Resource/signing heuristics still inspect the original workload tasks as well as the aggregate task, preventing an aggregate name from hiding packaging/test/lint work.

## Validation performed

- `python3 -m py_compile devtool/android_integration.py devtool/project_runners.py devtool/cli.py devtool/target_seal.py`
- `PYTHONPATH=. pytest -q tests/test_gradle_android_task_optimization.py tests/test_rem15_devtool_target_final_seal.py`
- Broader focused suite: `332 passed, 8 subtests passed`
- `devtool final-seal --strict`
- `devtool final-seal --target devtool --strict`
- `devtool final-seal --target android_hud --strict`
