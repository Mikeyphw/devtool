# Phase 41: Git Operation Trace

Phase 41 adds a durable timeline for repository manager Git actions. It records lifecycle breadcrumbs outside Git stdout/stderr so status parsers, diffs and command output remain clean.

## Trace checkpoints

- `GIT_OPERATION_QUEUED`
- `GIT_OPERATION_STARTED`
- `REPO_EXECUTION_PLAN_BUILT`
- `REPO_PATH_CONFIRMED`
- `GIT_BINARY_CONFIRMED`
- `GIT_COMMAND_STARTED`
- `GIT_OUTPUT_RECEIVED`
- `GIT_OPERATION_FINISHED`
- `GIT_OPERATION_FAILED`
- `REPO_PATH_CONFIRMATION_FAILED`
- `GIT_BINARY_CONFIRMATION_FAILED`
- `REPO_GIT_ROOT_CONFIRMATION_FAILED`

## Behavior

For queued repository operations, Devtool now performs a quiet executor preflight before the actual Git command starts. The preflight verifies the selected namespace can see the repository path, a Git binary is available, and the path is a Git worktree. If that fails, the operation stops before mutation and records a precise before-Git failure.

## Evidence surfaces

- Repository Activity events
- Debug events
- Structured synthetic run logs
- Repository support bundle entries
- Copy Git trace button for active and completed operations

Trace evidence is never printed into Git stdout/stderr by the wrapper itself.
