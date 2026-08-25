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
