# Devtool Gradle task execution telemetry + parallel log collapse v1

## Purpose

Gradle `--console=plain` can print the same bare `> Task :path` header more than once when a still-running parallel task resumes producing output after another task. Those repeated context headers look like repeated task execution even though Gradle's build graph executed the task only once.

This overlay makes that distinction explicit without sacrificing raw evidence.

## Behavior

- The default non-verbose human stream shows the first bare header for a task and collapses later bare headers for the same task as resumed output.
- Outcome-bearing task headers remain visible, including `FAILED`, `UP-TO-DATE`, `FROM-CACHE`, `SKIPPED`, and `NO-SOURCE`.
- `--verbose` remains an opt-in raw child stream and does not collapse repeated headers.
- Captured process output and the live evidence log retain the complete child stream for diagnostics.
- Structured Gradle `task-start` events are counted separately. Repeated structured starts increment `duplicate_execution_start_count`, which is the execution-anomaly signal.
- Requested task multiplicity is preserved long enough to report accidental duplicate requests before the tracker exposes the unique requested-task list.
- Tasker/HUD phase progress now persists task-execution telemetry.

## Telemetry fields

- `requested_duplicate_count`
- `requested_duplicate_tasks`
- `task_execution_count`
- `task_completed_count`
- `task_resume_count`
- `task_resume_counts`
- `task_header_count`
- `task_header_counts`
- `task_outcomes`
- `duplicate_execution_start_count`
- `duplicate_terminal_header_count`

A concise terminal line is emitted after a Gradle process, for example:

`Devtool Gradle task telemetry: requested=12 unique duplicates=0; executions=650 unique; completed=538; resumed_headers=20; duplicate_execution_starts=0`

## Replay of the supplied StudyFlow ATT12 log

The supplied ATT12 final-seal log was replayed through the new parser:

- Gradle task headers: 676
- unique observed task executions: 650
- repeated bare headers classified as resumed output: 20
- repeated structured task starts: 0
- `:verifyAtt05WebAttentionTests` resumed headers: 14
- `:verifyAtt05WebAttentionLint` resumed headers: 2
- one resumed header each for `:core:link:compileKotlin`, `:app-tv:compileDebugKotlin`, `:desktopApp:compileKotlin`, and `:app:compileReleaseKotlin`

This confirms the original visual duplication was predominantly parallel output interleaving rather than multiple Devtool Gradle invocations or duplicate Gradle task execution.

## Documentation

The human and AI-agent exhaustive guides were regenerated from the guide generator. The new sections explain how to interpret resume headers versus duplicate execution starts and which telemetry fields agents should consume.

## Validation

Focused regression gate after implementation:

- 240 tests passed
- `python3 tools/generate_complete_feature_guides.py --check` passed
- complete guide coverage remains current at 136 command parsers
