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

UI03/UI04 centralize frontend preferences/profiles plus semantic tokens, icons, and copy on top of the UI Core state/action/navigation contract. UI05/UI06 add capability/fallback negotiation, renderer-neutral responsive intent, canonical progress hierarchy, and structured diagnostic presentation. See [`../UI_PREFERENCES_AND_TOKENS.md`](../UI_PREFERENCES_AND_TOKENS.md), [`ui-capabilities-progress.md`](ui-capabilities-progress.md), and [`ui-core.md`](ui-core.md).

## UI renderer migration

UI09/UI10 complete GTK/Desktop and Android Live HUD migration onto UI Core. Renderers consume semantic actions/navigation, presentation, capabilities/responsive intent, hierarchical progress, structured diagnostics and notification policy. Platform widgets/layout remain native, and legacy machine/bridge fields remain compatibility fallbacks. See `docs/architecture/ui-desktop-android-migration.md`.

## External machine state and discoverability

UI11/UI12 complete the shared UI contract at the automation boundary. Machine JSON, IPC and Tasker keep their legacy envelopes/fields for compatibility while publishing an additive schema-v2 semantic machine state covering workspace/selection, operation/workflow/test selection, progress, diagnostics, actions, navigation, notifications, presentation, capabilities and responsive intent. IPC protocol v2 adds generic semantic action dispatch while retaining the legacy cancellation path. Config-aware `completion-state` provides one authoritative discovery API for shells/editors, and `config explain`, provenance-aware `config resolve`, and conservative `config migrate` replace ad-hoc config scraping/migration. See [`ui-machine-completion-config.md`](ui-machine-completion-config.md).

## Final compatibility/documentation pass

FS01/FS02 make compatibility itself inspectable. `devtool compatibility --json` is the release-facing registry of retained legacy boundaries; `config migrate` canonicalizes every safe repository/global config shim found by the audit while refusing ambiguous rewrites. Canonical examples and migration documentation are consolidated under `docs/EXAMPLES.md`, `docs/MIGRATION_1.1.md`, and `docs/BACKWARD_COMPATIBILITY.md`. FS03 uses these contracts as final-seal inputs.

## Repository-wide final seal

FS03 turns the completed roadmap into a reproducible release invariant. `devtool final-seal --strict` audits canonical repository configuration, compatibility/schema registries, roadmap report/test coverage, canonical examples/documentation, completion synchronization, target roots, backup hygiene, and release discoverability. The side-effect-free seal complements `devtool validate --profile final-seal` and artifact validation, which execute the runtime/build/test evidence. See [`../FINAL_RELEASE_SEAL.md`](../FINAL_RELEASE_SEAL.md).
