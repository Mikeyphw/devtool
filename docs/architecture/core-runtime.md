# Core runtime, events, results, and logging

Devtool's architecture runtime has one canonical structured result type: `devtool.core.CommandResult`. Existing `OperationResult` values remain the detailed runner record and expose `to_command_result()` for the canonical API boundary. Subprocess results and exceptions have equivalent adapters.

The process-global event bus emits build, task, warning, artifact, and overlay lifecycle events. Exact-type subscribers and all-event subscribers are supported. Built-in consumers cover JSON/API collection, bounded TUI state, Python logging, and optional verbose/trace console output.

Global output modes are:

- `--verbose` — stream command output and human event traces.
- `--quiet` — suppress normal Rich output while preserving errors.
- `--trace` — enable detailed event logging and full Python tracebacks.
- `--json` — emit the universal machine envelope, including a canonical `result` object and captured architecture `events`.

Architecture feature flags live in `devtool.features` because `devtool.config` is an established module rather than a package. They can be overridden under `[features]` in `.devtool.toml` or by `DEVTOOL_FEATURE_<NAME>` environment variables.

## JSON compatibility boundary

Universal `--json` wraps ordinary commands in the schema-versioned invocation envelope. Commands that already emit Devtool's stable validation-control machine envelope (`api_version`, `accepted`, `action`, `data`) pass through unchanged. This prevents double envelopes and keeps machine-file/stdout equality stable while still giving every non-native command a consistent JSON surface.


## MP09/MP10 execution planning

The workspace workflow planner produces an expanded `ExecutionGraph`; runner planning turns its nodes into ordinary `CommandSpec` objects; `workflow_scheduler` then annotates read/write/resource scopes, produces safe scheduling waves, and executes independent nodes when enabled. The scheduler never bypasses runner-specific environment preparation or diagnostics. `devtool plan` serializes the same graph/schedule model used by runtime, keeping observability and execution on one truth source.


## UI01/UI02 semantic UI boundary

Runtime results and live progress are presentation-independent facts. `devtool.ui_core` converts those facts into a versioned `UISnapshot` and resolves common actions/navigation from central registries. This layer deliberately contains no GTK widgets, ANSI rendering, Android views, colors, typography, or responsive layout. Frontends consume the semantic contract while retaining native rendering behavior.

Tasker state and IPC transport the additive `ui` snapshot, terminal HUD snapshots publish it, desktop Control Center/HUD state preserves it, and the Android HUD bridge receives serialized action/navigation metadata. `devtool ui-contract` exposes the registry contract for debugging and integrations.
