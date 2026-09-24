# WRAP-01 — Repository Wrapper Configuration Contract

## Scope

WRAP-01 establishes the repository-owned configuration model for the future `devtoolw` interface. It intentionally does **not** add wrapper argv resolution, `devtool invoke`, wrapper execution, launcher generation, SSH/chroot dispatch changes, or EXO invocation provenance. Those remain assigned to later WRAP overlays.

## Delivered

- Added typed wrapper models to `devtool.models`:
  - `WrapperConfig`
  - `WrapperCommandConfig`
  - `WrapperOptionConfig`
  - `WrapperPositionalConfig`
- Added `devtool.wrapper_config` as the dedicated schema/parser/serializer boundary.
- Added repository-local `[wrapper]` loading to `ProjectConfig`.
- Wrapper configuration is deliberately sourced from the repository TOML before/independently of global default merging.
- Added `wrapper` to global-config repo-scoped diagnostics, preventing `~/.config/devtool/config.toml` from being treated as a source of repository wrapper commands.
- Added schema-v1 validation for:
  - canonical native Devtool commands versus repository workflow commands;
  - command and option aliases;
  - target/group/all-target scope exclusivity;
  - target/group existence;
  - statically scoped workflow existence;
  - deterministic workflow scope when no explicit scope is declared;
  - boolean/string/integer/float/choice/path option types;
  - repeatable values, required values and defaults;
  - choice defaults;
  - conflicts/requires references;
  - path base (`cwd`/`repo`);
  - global/command/passthrough argv token templates;
  - token templates limited to `{value}` with no shell formatting/evaluation;
  - positional ordering/repeatability rules;
  - unknown schema fields;
  - native passthrough opt-in;
  - workflow passthrough rejection in schema v1.
- Added `load_wrapper_project_config()` so future wrapper dispatch can inspect a mixed/multi-target repository before choosing a target or project runner.
- Added wrapper data to `config resolve` and `config explain` machine output.
- Added wrapper summary rows to `config explain` and `config doctor` human output.
- Added wrapper contract and health check to the general `doctor_payload()` machine surface.

## Security / architecture boundaries

- No wrapper command executes during config loading.
- No shell strings, `eval`, or `sh -c` generation were introduced.
- No generic environment or secret wrapper binding was introduced in schema v1.
- Wrapper configuration cannot be injected by global config.
- Native command aliases such as `b` are rejected in repository contracts; canonical names such as `build` are required.
- Repository workflow bindings must point to declared workflows on every target selected by a static target/group/all-target scope.
- `WrapperResolution` remains deferred to WRAP-03, where its actual deterministic argv shape will be defined.

## Audit loop

The implementation was audited against the revised WRAP roadmap after the first pass. The loop found and corrected four gaps before packaging:

1. Workflow commands initially validated target/group existence but not workflow existence on every statically selected target.
2. The schema initially had only command-level argv and passthrough bindings; `global_argv` was added now to avoid a schema-v1 compatibility change when WRAP-03 needs pre-command Devtool options.
3. The first implementation did not yet expose a safe config-loading path for a mixed multi-target repository before target selection; `load_wrapper_project_config()` now explicitly permits both ambiguities while ordinary loads remain strict.
4. Wrapper information was visible through config tooling but not the general doctor machine payload; doctor now exposes the wrapper contract and a project wrapper health check.

A final code audit confirmed the WRAP-01 boundary remains configuration-only.

## Verification

Focused WRAP-01 contract suite:

- 15 passed.

Selected compatibility suite covering repository/config discovery, global config policy, target/job/workflow contracts, workflow DAG runtime, cross-target workspace groups, completion state, and machine/config integration:

- 125 passed.

Additional checks:

- `python -m py_compile` passed for changed Python modules/tests.
- `python -m compileall -q devtool` passed.
- Ruff was not available in the supplied environment, so no Ruff result is claimed.

## Validation policy

Per the WRAP roadmap, WRAP-01 is an intermediate overlay and does not request the broad Devtool validation gate. The artifact therefore ships with validation disabled. WRAP-05 is Gate 1.

## Before-commit self-host refresh

The overlay declares a `before_commit` finalize hook that runs:

```sh
pip install -e "$HOME/Code/devtool"
```

This preserves the established Devtool overlay campaign convention that the installed editable Devtool is refreshed before the overlay commit.

## v2 finalize-hook write-boundary correction

The first packaged v1 artifact was rejected during `before_commit` finalization because the required editable-install refresh regenerated ignored setuptools metadata at `devtool.egg-info/SOURCES.txt`. Devtool's hook write-boundary correctly treated that as an undeclared repository mutation and rolled the transaction back.

v2 keeps the required:

```sh
pip install -e "$HOME/Code/devtool"
```

but snapshots the complete `devtool.egg-info` directory before the install and restores its exact prior state before the hook exits. If the directory did not exist before the hook, a newly generated directory is removed. Cleanup is attached to `EXIT`, so it also runs if pip fails.

Artifact-level audit coverage additionally verified:

- source-inventory hashes against the untouched supplied snapshot: PASS;
- focused WRAP-01 tests after fresh replay: 15 passed;
- fake-pip mutation with no pre-existing egg-info: zero persistent undeclared changes;
- fake-pip mutation with pre-existing egg-info: original files restored and generated files removed;
- cleanup after a failing real pip invocation in the audit sandbox: PASS.

The hook does not declare `devtool.egg-info` as a writable artifact path; generated package metadata is deliberately restored instead of legitimized as overlay output.
