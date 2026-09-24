# FRX-11 Machine/UI/Doctor/Observability Convergence Report

## Scope

FRX-11 converges federation inspection onto one read-only projection. It does
not add execution semantics or a second run store.

## Delivered

- Added `devtool/federation_observability.py` with a schema-versioned static and
  runtime federation projection.
- Added `devtool federation status|summary [--run RUN_ID] [--json]`.
- Upgraded ordinary `status --json` and `doctor --json` federation payloads to
  the unified projection while preserving legacy `healthy` and repository
  boundary fields.
- Added safe runtime projection of parent/child EXO lineage, repository-qualified
  child nodes, operational reference kinds, and provider-resolved remote path
  mappings. Raw argv/invocation metadata is intentionally excluded.
- Added the additive `federation` field to shared UI, machine-v2, HUD canonical
  projection, and desktop semantic state without changing their schema versions.
- Added one federation panel to the TUI dashboard/live run presentation instead
  of another navigation destination.
- Added one federation section to the desktop Control Center snapshot.
- Expanded doctor checks for canonical enrollment roots, workflow plan/cycle
  health, wrapper/TLJ public contracts, remote mapping evidence, and unified
  observability health.
- Added config-aware completion values and zsh completions for federation
  repositories/workflows and synchronized `completions/_devtool`.

## Safety and compatibility

- Observability is read-only and never executes child workflows/jobs/hooks.
- UI payloads do not expose raw child argv or wrapper option values.
- Existing machine schema remains v2; UI schema remains v1.
- Non-federated repositories remain valid and retain additive empty federation
  state only.
- TUI/desktop surfaces reuse canonical EXO/federation truth rather than
  reconstructing their own graph.

## Audit

- FRX-11 focused tests: 8 passed.
- FRX-01..11 + machine/completion/TUI/desktop/doctor compatibility slice:
  181 passed, 32 subtests passed.
- `python -m compileall -q devtool`: pass.
- Checked-in zsh completion equals `ZSH_COMPLETION`: pass.
