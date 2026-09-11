# Devtool architecture overview

Devtool is organized around a compatibility-first runtime whose public CLI remains stable while internal models become increasingly structured.

## Core execution layers

```text
CLI / config
    ↓
Workspace + targets + jobs
    ↓
Workflow planner / ExecutionGraph
    ↓
Safe scheduler + reservations
    ↓
Runner registry / execution environments
    ↓
Command results + runtime events
    ↓
UI Core semantic state
    ↓
TUI / HUD / GTK / Android bridge / IPC / Tasker
```

The polyglot workspace model separates target identity, product platform, runner, and execution environment. Reusable jobs and target workflows expand into one dependency graph, including cross-target prerequisites and named workspace groups. Affected-target analysis and graph-aware preflight operate on that same model. Safe scheduling is sequential by default and only overlaps dependency-ready nodes whose declared scopes prove compatibility.

## Structured runtime boundary

`devtool.core.CommandResult` is the canonical lightweight command-result boundary; detailed runner results remain compatible through adapters. The event bus publishes build/task/warning/artifact/overlay lifecycle data to JSON/API, TUI, logging, and trace consumers. Universal `--json` provides a versioned invocation envelope while preserving stable native machine envelopes for validation-control APIs.

## Semantic UI boundary

UI01/UI02 add `devtool.ui_core`. Runtime/domain code owns facts, UI Core owns semantic presentation state, and individual frontends own native rendering. The versioned `UISnapshot` normalizes workspace/selection, operation lifecycle, progress, diagnostics, actions, and navigation. Central action/navigation registries assign stable IDs and presentation metadata so TUI, GTK, Android bridges, HUD, IPC, and Tasker do not independently redefine the same meaning.

See [`ui-core.md`](ui-core.md), [`core-runtime.md`](core-runtime.md), and the workflow architecture documents for the detailed contracts.

## Shared presentation policy

UI03/UI04 centralize frontend preferences/profiles plus semantic tokens, icons, and copy on top of the UI Core state/action/navigation contract. See [`../UI_PREFERENCES_AND_TOKENS.md`](../UI_PREFERENCES_AND_TOKENS.md) and [`ui-core.md`](ui-core.md).
