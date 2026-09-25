# Federated repositories

## FRX-01: repository identity contract

FRX-01 establishes repository identity without enabling federation or child-repository discovery.

A loaded `ProjectConfig` now owns a canonical repository identity containing:

- canonical repository root;
- stable identity fingerprint for that repository instance;
- display name;
- repository config path;
- Git top-level and Git common-dir identity when available;
- a digest of the Git origin URL when available (the URL itself is never exposed).

The identity is serialized into config-resolution output, workflow planning metadata, executable workflow `CommandSpec` metadata, and EXO run snapshots. This gives later federation work one identity primitive instead of relying on filesystem strings at each surface.

### Config ownership

Repository identity distinguishes the requested project root from the configuration-owning repository root. Existing Devtool behavior where a parent/workspace `.devtool.toml` governs a deeper project remains valid and resolves to the parent's repository identity.

A config owned by a descendant or unrelated directory is rejected. This prevents reverse/cross-repository config borrowing while preserving established workspace behavior.

### What FRX-01 does not do

FRX-01 does not add `[federation]`, discover child repositories, create cross-repository DAG edges, or execute child repositories. Those begin in later FRX overlays.

## FRX-02: explicit federation configuration

Federation enrollment is repository-owned and opt-in. Merely placing another
Git repository below a Devtool repository does not enroll it.

```toml
[federation]
schema = 1

[federation.repositories.android-app]
path = "../android-app"

[federation.repositories.optional-tool]
path = "tools/optional-tool"
required = false

[federation.groups.product]
repositories = ["android-app", "optional-tool"]
```

Relative paths are resolved from the **configuration-owning repository root**,
not from the process cwd or a deeper requested project root. Required children
must exist as directories at config-load time; optional children may be absent
and remain visible as unavailable enrollment records. Self-enrollment, duplicate
canonical roots (including symlink aliases), unknown fields, unsupported schema
versions, and invalid group references fail closed.

FRX-02 deliberately does not inspect child `.devtool.toml` files or execute
child work. Repository discovery/ownership resolution begins in FRX-03.

## FRX-03: discovery and repository boundaries

FRX-03 resolves the explicit FRX-02 enrollment records into authoritative child
repository boundaries. Discovery remains strictly opt-in: nested Git
repositories or `.devtool.toml` files that are not enrolled are ignored.

Each enrolled repository is resolved independently through:

1. the explicitly requested federation repository name;
2. the enrolled canonical child root;
3. that child's own root `.devtool.toml`, when present;
4. otherwise child-local detected defaults;
5. the child's own workspace/default target.

Parent `--target`, workspace selection, and parent config do not flow into the
child. In particular, a nested child without its own `.devtool.toml` does **not**
borrow an ancestor parent's config during federation discovery. Ordinary
non-federated Devtool config discovery retains its existing ancestor lookup.

Repository resolution records the canonical child identity, config path,
selected target, runner/platform, parent-relative relation (`nested`,
`external`, or `ancestor`), and a boundary status/detail. Symlink enrollments are
canonicalized before identity resolution.

FRX-03 also exposes a first-class path-owner resolver for repository-aware
consumers. It canonicalizes the requested path, walks only the recursively
**explicit** enrollment graph, and selects the longest containing enrolled root.
An explicitly enrolled grandchild therefore owns paths below its root instead of
its parent; an unenrolled nested Git checkout never becomes a federation member.
Symlink paths resolve to canonical ownership, paths outside the known federation
roots have no owner, and duplicate/ambiguous canonical ownership fails closed.
FRX-11 status/doctor projections consume the same resolver rather than deriving
ownership independently.

The public inspection surface is:

```text
devtool federation list
devtool federation inspect <repository>
```

Both commands have machine-readable JSON forms. `doctor --json` and
`status --json` also expose the same federation boundary payload, while their
human surfaces include repository boundary summaries.

FRX-03 still does not create cross-repository DAG edges or execute child work.
That begins with FRX-05 after the FRX-04 boundary gate.

## FRX-04 boundary gate

Before cross-repository planning is introduced, Devtool exposes a read-only
boundary qualification gate:

```bash
devtool federation preflight
devtool federation preflight --json
```

`gate1` is an alias for automation compatibility. The gate validates the owner
repository identity, every enrolled child boundary, isolated child config
reloads, wrapper and target-local discovery, and EXO repository-identity
persistence. Optional missing repositories are reported but do not fail the
gate. Required missing, ambiguous, identity-colliding, or config-leaking child
repositories fail closed.

A repository without `[federation]` passes the same gate in `standalone` mode;
FRX remains opt-in.

## FRX-05 federated plan graph

FRX-05 introduces planning-only cross-repository DAGs. It does not execute
child commands.

Federation workflows are repository-owned and can use the compact prerequisite
form:

```toml
[federation.workflows.release]
depends_on = ["backend::prepare-release", "android-app::release"]
```

The shorthand expands both child workflows and joins them at a synthetic,
non-executable `@federation::release` aggregate node.

For explicit cross-repository edges, use structured steps:

```toml
[federation.workflows.release]
steps = [
  { id = "backend", ref = "backend::prepare-release" },
  { id = "android", ref = "android-app::release", depends_on = ["backend"] },
]
```

Each `repository::workflow` resolves the enrolled child through the FRX-03
boundary resolver, uses only that child's selected/default target, requires the
named workflow to exist, expands the child's existing workflow DAG, and prefixes
all child graph nodes with the federation repository name. Cross-repository
step dependencies are attached to the downstream child roots.

Planning is available through:

```text
devtool federation plan <workflow>
devtool federation plan <workflow> --json
```

The graph is deterministic, serializable, cycle-checked, and includes child
repository identity metadata on every executable child node. Ambiguous child
targets, missing child workflows, unavailable repositories, ownership changes,
unknown dependencies, and dependency cycles fail closed. The planner is
read-only and must not execute a child job, hook, or runner action.

## FRX-X01: target-qualified child workflow identity

FRX-X01 extends the sealed FRX-05 reference grammar without changing the
legacy form. Federation workflow references may now be either:

```text
repository::workflow
repository::target::workflow
```

`repository::workflow` remains backward compatible and resolves through the
child repository's selected/default target. `repository::target::workflow`
selects the named child target explicitly, including a non-default target in a
multi-target repository. The parser records whether the target was explicit;
target existence remains a child-owned planning check rather than a parent
configuration-time discovery side effect.

Once a child target is resolved, the planner normalizes both forms to one
canonical invocation identity: `repository::resolved-target::workflow`. Every
non-synthetic machine plan node exposes that value as `federation_ref`, and
plan metadata retains both the normalized identity and original requested ref.
The existing execution coordinator already consumes the planner's resolved
target, so a qualified reference re-enters the child with the explicit
`--target` selection. FRX-X02 is responsible for propagating that canonical
identity through all execution lineage, provider, UI, doctor, and summary
surfaces.

## FRX-06: EXO parent/child execution lineage

Federated execution uses a coordinator run plus one authoritative EXO child run
per configured federation workflow step.  Child runs never reuse the parent run
id.  The parent node records the child run id, while the child execution
snapshot stores additive `run_lineage` metadata containing the parent run/node,
federation workflow/step, repository alias, source/effective repository
identity, and execution-environment request.

`devtool federation run <workflow>` is deliberately sequential in FRX-06.  It
honors explicit federation dependencies, blocks dependent steps after a failed
prerequisite, preserves the first concrete child exit code, propagates
cancellation, and restores the caller's execution environment after every
recursive child invocation. Exit `130` and `KeyboardInterrupt` are authoritative
cancellation signals: the parent enters a cancelled terminal state, requests
cancellation of the active child EXO run, records that child's cancelled lineage
for later inspection, and does not schedule otherwise-independent later branches.
Unscheduled branches are explicitly blocked by cancellation; the parent still
never rolls back child-owned state. Direct wrapper/job federation execution uses
the same cancellation semantics. The child itself still runs through canonical
`devtool invoke`, so ordinary workflow, runner, EXO, logging, and validation
semantics remain authoritative inside that repository.

## FRX-07: wrapper and target-local-job federation bridge

`devtool federation exec <repository> -- wrapper <args...>` resolves the child
repository's own wrapper contract, preserves WRAP's canonical single re-entry
and wrapper invocation provenance, and adds FRX-06 federation lineage without
forwarding parent repository/target routing controls.  Parent EXO nodes store
only the wrapper's redacted argv projection, so sensitive wrapper option values
are not copied into federation orchestration evidence.

`devtool federation exec <repository> -- job <reference>` resolves a child-owned
repository or target-local job and sends the resulting normal `CommandSpec`s
through the existing runner pipeline.  An unqualified job prefers the child's
selected/sole target-local contract, while `<target>:job:<name>` is strict and
never falls back to repository scope.  `--plan` performs the same resolution
without execution.

## FRX-08: execution and contract Gate 2

FRX-08 seals the local cross-repository execution contract before remote
transport is allowed to build on it. The read-only gate is exposed as:

```text
devtool federation gate2
devtool federation gate2 --json
```

`execution-preflight` is an alias. The gate requires the FRX-04 boundary gate,
rebuilds configured federated plans, verifies deterministic dependency edges
and current child repository identities, and runs an in-memory EXO authority
probe proving that parent and child run ids remain distinct and that corrupt
lineage is rejected. `doctor` exposes the same qualification as
`federation/gate2`.

Execution is hardened at the same boundary: immediately before a federated
child is invoked, Devtool re-resolves the child and requires its canonical
repository identity/root to match the plan. After execution, the authoritative
child EXO snapshot must persist the expected repository identity, parent run id,
and exact child run id. A moved/replaced child or mutated lineage therefore
fails closed rather than being accepted as the planned execution.

Repositories without `[federation]` remain valid and report `standalone` mode.

## FRX-09: remote execution repository identity

Federation transport reuses each execution provider's existing authoritative
path mapping rather than assuming that local and remote repositories have the
same path. For federation child work, execution metadata now carries the source
repository, child repository identity/fingerprint, local and provider-resolved
remote roots, execution environment/provider, parent/child lineage, artifact
return root, and the mapping mechanism that produced the remote path.

Provider integration follows the existing execution contracts:

- Termux host to chroot uses the configured Termux/chroot path map.
- PRoot uses the established bind/repository mount mapping.
- SSH uses the selected managed/existing remote workspace root.
- SSH+OCI uses the resolved OCI guest workspace rooted through the SSH provider.
- Local execution retains identity mapping without inventing a remote path.

Remote-required providers fail closed when no explicit provider-resolved child
root is available, when lineage names a different effective repository, or when
the local execution root no longer belongs to the planned repository identity.
A mapped path alone is not accepted as identity. Devtool derives portable,
secret-neutral attestation from the child root `.devtool.toml` byte digest when
present and the normalized Git-origin digest when present. Remote federation
requires at least one such signal and never compares local and remote absolute
paths as identity.

Command-mode SSH, Termux-to-chroot, PRoot, and SSH+OCI execute a fail-closed
pre-command probe against the provider-resolved repository root before the child
phase. Whole-run SSH and chroot delegation require the same attestation signal,
forward it with federation lineage, and the remote Devtool re-verifies the
repository after config ownership is resolved. Missing, truncated, stale, or
mismatched evidence fails before child work begins. Managed `~`/`$HOME` SSH
workspace roots retain safe HOME-relative expansion during the probe. Returned
SSH/OCI producer evidence includes the child identity, mapped roots, lineage,
attestation metadata, and actual artifact-return location so diagnostics and
artifacts remain attributable to the owning repository.

## FRX-10: artifacts, hooks, validation, and transactional boundaries

Federation orchestration does not acquire ownership of child operational state.
When a canonical child run is active, the existing artifact registry, apply
transaction journal, validation-session context, and embedded-hook event record
an additive `federation_owner` object containing the child repository alias,
repository fingerprint, canonical owner root, parent run/node, and authoritative
child EXO run id. Non-federated execution keeps its existing payload shape.

The parent coordinator aggregates only compact references after a child run:
artifact-registry record ids/paths, transaction ids/status, validation-session
ids/checkpoint keys, hook-event summaries, diagnostic ids/log references, and a
child resume checkpoint. The parent never imports a child's rollback journal,
validation plan, hook write-set, or artifact-registry contents into its own
transaction/validation state.

Child apply transactions continue to use the existing repository-keyed XDG
transaction root and child rollback policy. Parent cancellation may request
cancellation of the authoritative child EXO run through the normal IPC control
path, but the federation coordinator does not invoke rollback or cleanup on the
child's behalf. Validation is likewise repository-local: only child validation
sessions carrying the matching child owner/run metadata can appear in child
operational references, so a successful parent validation session cannot
satisfy a child gate.

Each executed child also receives a resume checkpoint containing its canonical
repository root, repository fingerprint, and Git revision when available.
Resume must re-resolve the enrollment and reject a moved/replaced repository or
changed Git revision. Child EXO snapshots remain independently recoverable: if
a parent snapshot is absent, lineage can identify child runs as orphaned
children without rewriting their operational state.

## FRX-11: machine/UI/doctor observability convergence

Federation observability is a read-only projection over the already-authoritative
FRX contracts. `devtool federation status` and `--json` combine child boundary
resolution, public wrapper/TLJ contracts, deterministic workflow-plan health,
Gate 1/Gate 2 status, and currently observable EXO federation lineage into one
schema-versioned payload. The projection deliberately excludes raw argv and
arbitrary invocation metadata so WRAP secret-redaction guarantees survive when
state is copied into dashboards or desktop IPC.

The same runtime federation projection is embedded into shared UI state,
machine state, HUD/desktop canonical execution projections, and the GTK
semantic view. Coordinator runs expose repository-qualified child nodes, child
run ids, statuses, safe transport mappings, and operational reference kinds.
Child runs expose their parent run/node and repository identity. This keeps the
TUI and desktop surfaces repo-aware without creating another navigation tree or
new execution truth source.

The terminal dashboard adds one compact Federation panel when the repository is
federated. The desktop Control Center snapshot adds one `federation` section,
and machine state keeps schema v2 while adding an optional `federation` field.
Repositories without federation continue to emit an empty/additive field and do
not change execution behavior.

`doctor` now cross-checks the unified projection: enrollment/config boundaries,
canonical root uniqueness, canonical path ownership from the FRX-03 owner
resolver, workflow-plan/cycle health, child wrapper/TLJ public contracts, Gate
1/Gate 2, observed remote path mappings, and orphan child runs.
Completion state and zsh completion expose federation repository/workflow names
for `list`, `inspect`, `status`, `plan`, `run`, and `exec` commands. The checked-
in completion file is kept byte-identical to `devtool.completion.ZSH_COMPLETION`.

## FRX-X02: canonical target/workflow execution identity convergence

Target-qualified planning identity is carried unchanged through execution and
observability. Every federated workflow child has one canonical
`repository::target::workflow` reference after planning, even when the source
configuration used the backward-compatible `repository::workflow` form. The
original requested reference is retained separately for diagnostics.

The child EXO lineage persists `repository`, resolved `target`, child workflow,
canonical `federation_ref`, and `requested_ref` together with the existing parent
run/node and repository fingerprint. Parent federation nodes use the same
canonical reference as their label and metadata. The child re-entry environment
forwards those fields, and the child execution store must persist them before the
coordinator accepts the child snapshot as authoritative.

Command-mode transport metadata and whole-run SSH/chroot delegation preserve the
same target/workflow identity. When any new target-qualified lineage fields are
present, transport validates them as a complete tuple and rejects a canonical
reference that does not equal `repository::target::workflow`. Older persisted
lineage without these additive fields remains readable. SSH/OCI return producer
evidence also retains the child target, child workflow, canonical ref, and
requested ref so returned artifacts remain attributable to the exact federated
child contract.

Direct WRAP/TLJ federation bridges do not pretend wrapper/job contracts are child
workflows. They instead persist the resolved child target plus a distinct
`child_contract`/`contract_ref` such as `repository::target::wrapper:build` or
`repository::target::job:lint`. Explicit target-local-job references use the
actually resolved target rather than the repository default.

The shared federation observability projection exposes canonical refs in workflow
configuration health, live parent/child EXO state, doctor/machine output, CLI
status, HUD projections, and TUI federation panels. This prevents two workflows
with the same name on different child targets from collapsing into one UI or
provenance identity.

## FRX-12: final seal and release qualification

The federation campaign closes with a read-only final qualification surface:

```text
devtool federation final-seal
devtool federation final-seal --json
devtool federation seal
```

The final seal exposes one check for each of the fourteen roadmap promises:
configuration contract, repository identity, explicit discovery, boundary
isolation, graph planning, cycle rejection, EXO parent/child lineage, WRAP
bridge, target-local-job bridge, local/remote repository identity propagation,
artifact/hook/validation/transaction ownership, machine/UI/doctor truth,
standalone backward compatibility, and documentation/completion parity.

FRX-12 also performs a recursive topology audit that follows only configured
`[federation.repositories]` edges. It never scans for Git repositories. This
closes deep registration cycles such as `A -> B -> C -> A` while preserving the
explicit-enrollment rule. Optional missing children remain optional; required
children, identity collapse, boundary borrowing, or cyclic explicit enrollment
fail the seal.

The twenty required adversarial safety cases are also bound to a machine-readable
`case -> promise -> executable evidence` contract. Source qualification resolves
every referenced pytest node, and FRX-12's top-level status fails if a required
case is missing or its evidence cannot be resolved. This evidence contract does
not create a fifteenth qualification area: the public roadmap inventory remains
exactly fourteen checks.

The final release validation replays all FRX-01..12 contract suites together
with WRAP, target-local-job, EXO, remote-provider, operational-boundary,
UI/machine, doctor, and completion coverage. Repositories without federation
remain in standalone mode and keep the existing build/test/validate/wrapper/TLJ
execution contracts unchanged.


## FRX-X03: executable federation groups

FRX-X03 makes federation groups an execution surface without creating a second
planner or scheduler. Executable groups compile into a synthetic federation
workflow and then use the existing FRX planner/coordinator unchanged. This
preserves repository ownership, target resolution, canonical
`repository::target::workflow` identity, EXO parent/child lineage, cancellation,
remote attestation, and child-owned workflow behavior.

Legacy repository-only groups remain valid metadata:

```toml
[federation.groups.product]
repositories = ["backend", "android"]
```

They are not executable because repository membership alone does not identify
work. Attempting `federation run --group product` fails closed with guidance to
add executable members.

Executable groups may use a shorthand array:

```toml
[federation.groups]
ready = [
  "validate-ready",
  "chrootctl::chrootctl_root_module::package",
]
```

or table form when repository metadata and executable membership are both
useful:

```toml
[federation.groups.release]
repositories = ["backend", "android"]
members = ["workflow:validate-ready", "group:package-ready"]
```

Members normalize to exactly one of three kinds:

- `workflow:<name>` — a named `[federation.workflows]` workflow;
- `group:<name>` (or `@name`) — a nested executable federation group;
- `repository::workflow` or `repository::target::workflow` — a direct child
  workflow reference using the FRX-X01 grammar.

A bare name is accepted only when it resolves unambiguously to one workflow or
one group. If a workflow and group share the same name, configuration must use
an explicit `workflow:` or `group:` prefix. Unknown members, duplicate normalized
members, group cycles, and executable groups that nest metadata-only groups fail
at configuration time. Overlapping expanded child graphs fail closed in the
normal FRX planner rather than silently executing duplicate work.

Planning and execution are available through:

```text
devtool federation plan --group <group>
devtool federation plan --group <group> --json
devtool federation run --group <group>
devtool federation run --group <group> --json
```

The group plan is represented internally as `group:<name>` and exposes
`subject_kind = "group"` plus the original group name in machine output. Group
runs persist `federation_group` in coordinator and child lineage, forward it
through whole-run/provider environments, retain it in transport/return-artifact
provenance, and expose executable-vs-metadata group details in federation
observability. Completion only offers executable groups for `--group`.
