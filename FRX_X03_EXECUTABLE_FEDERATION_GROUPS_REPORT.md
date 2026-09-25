# FRX-X03 Executable Federation Groups Report

## Scope

FRX-X03 makes `[federation.groups]` an executable orchestration surface without creating a second planner or scheduler. Groups compile into the existing FRX workflow contract, so normal repository ownership, target resolution, canonical child identity, EXO lineage, cancellation, remote attestation, and provider behavior remain authoritative.

FRX-X04 real cross-repository adoption/final re-seal is intentionally deferred.

## Delivered contract

- Preserves legacy repository-only groups as metadata-only groups.
- Adds executable group members in either shorthand array form under `[federation.groups]` or table `members = [...]` form.
- Supports normalized executable member kinds:
  - `workflow:<name>` for named federation workflows;
  - `group:<name>` / `@name` for nested executable federation groups;
  - `repository::workflow` and `repository::target::workflow` direct child refs.
- Bare names resolve only when unambiguous between group/workflow namespaces.
- Rejects unknown members, normalized duplicates, group cycles, ambiguous bare members, and executable groups nesting metadata-only groups.
- Overlapping expanded child DAG nodes fail closed in the normal FRX planner instead of silently executing duplicate work.
- Adds `devtool federation plan --group <name>` and `devtool federation run --group <name>`.
- Requires exactly one subject: positional federation workflow or `--group`.
- Exposes `subject_kind=group`, original `group`, and internal synthetic workflow `group:<name>` in machine plan/run output.
- Persists `federation_group` in coordinator and authoritative child EXO lineage.
- Carries group identity through child re-entry environment, transport metadata, remote producer identity, and observability projections.
- Adds executable-vs-metadata `group_details` to federation discovery/observability while preserving legacy repository-membership projections.
- Adds completion for executable groups only; metadata-only groups are not suggested for `--group`.
- Keeps the checked-in zsh completion byte-identical to runtime completion.

## Audit-loop findings fixed during implementation

1. **Metadata-only nested group:** an executable group could initially reference a legacy repository-only group and fail only during planning. Configuration now rejects this immediately.
2. **Child lineage authority:** group identity initially existed only on the coordinator/local lineage object. Child re-entry now receives `DEVTOOL_FEDERATION_GROUP`, and persisted child snapshots are rejected if the expected group identity is missing or mismatched.
3. **Remote provenance:** group identity is retained in federation transport and OCI/SSH return-producer provenance rather than disappearing after delegation.
4. **Observability gap:** legacy `groups` output only represented repository membership. Additive `group_details` now exposes executable members and mode without breaking existing consumers.
5. **Completion drift:** runtime zsh completion changed for `--group`; the packaged `_devtool` completion was synchronized and completion/manpage parity revalidated.

## Verification

Focused FRX-X03 acceptance:

- `tests/test_frx_x03_executable_federation_groups.py`: 12 passed.

Full FRX restart after implementation:

- `tests/test_frx*.py`: 152 passed.

Frozen cross-surface audit restart:

- FRX family
- completion/manpage parity
- completion-state consumers
- OCI return-artifact resilience/provenance
- SSH whole-run forwarding
- Termux/chroot forwarding
- invocation-origin metadata
- UI11 machine/completion/config surfaces
- TUI inspection

Result: **262 passed + 32 subtests**.

`python -m compileall -q devtool`: PASS.

The final audit restart produced **zero new material FRX-X03 gaps**.

## Compatibility and boundaries

- `[federation].schema` remains `1`; the extension is additive.
- Existing `[federation.groups.<name>] repositories = [...]` remains valid and non-executable.
- Existing `federation plan <workflow>` and `federation run <workflow>` remain unchanged.
- FRX-X01 `repository::target::workflow` semantics remain authoritative for direct refs.
- FRX-X02 canonical child target/workflow identity remains authoritative after group expansion.
- No historical FRX report is modified by this overlay.
- This is a non-gate detour overlay and must be applied with `--no-validate`.
- FRX-X04 remains responsible for real-repository adoption (`validate-ready`, `build-ready`, `test-ready`) and the detour final re-seal.

## Final artifact replay

The packaged overlay was validated against a fresh copy of the exact post-FRX-X02 baseline:

- Devtool artifact validation: PASS.
- Inventory source preconditions: **16/16**.
- Inventory post-apply hashes: **16/16**.
- Replayed `tests/test_frx*.py` plus completion/manpage parity: **161 passed + 32 subtests**.
- No material source change occurred after the zero-gap audit other than this evidence-report update.
