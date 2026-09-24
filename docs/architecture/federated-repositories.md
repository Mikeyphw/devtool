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
recursive child invocation.  The child itself still runs through canonical
`devtool invoke`, so ordinary workflow, runner, EXO, logging, and validation
semantics remain authoritative inside that repository.
