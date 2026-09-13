# Overlay 3 of 15 — CLI, typed configuration, workspace and target discovery

## Scope

This overlay closes the full S01 + S02 finding bucket from the final remediation roadmap. It hardens Devtool's CLI/config/discovery boundary before the later execution, transaction, artifact, Android, and UI overlays build on those semantics.

## Findings closed

- S01-01 — Global CLI defaults now use typed boolean coercion instead of Python truthiness, so strings such as `"false"` no longer enable flags accidentally.
- S01-02 — Universal JSON processing now strips only root-level `--json` while preserving command-local `--json` for native parsers and still enabling the machine envelope.
- S01-03 — `completion-state` can inspect configured targets/groups without requiring a default target in multi-target repositories.
- S01-04 — Malformed repo UI config reports against the repo config path instead of tripping an unbound source variable.
- S01-05 — runner passthrough splitting recognizes the expanded root/global option set before `--`.
- S01-06 — config migration parses TOML string assignments and re-renders migrated strings safely, preserving apostrophes and escaped quotes.
- S01-OBS-01 — Termux bootstrap destination architecture is resolved from Termux/Android ABI signals and malformed receipt schemas are ignored safely.
- S01-R2-01 — global config doctor now validates CLI table shapes and option values instead of accepting impossible global defaults silently.
- S01-R2-02 — doctor feature flags use the explicitly loaded config path rather than always reading the repository-root `.devtool.toml`.
- S01-R2-03 — toolchain selection honors an explicit empty environment mapping and doctor no longer aborts when explicit `mise` is unavailable.
- S02-01 — Android Gradle discovery recognizes version-catalog plugin aliases.
- S02-02 — independent nested Gradle roots with their own settings/wrapper are discovered even under a root Gradle build.
- S02-03 — recursive non-.NET project roots are discovered for Node, Rust, Go, Maven, CMake, Meson, and Make targets.
- S02-04 — marker discovery ignores `.devtool`, caches, build outputs, vendor, and other generated trees consistently.
- S02-05 — target names are strict identifiers and cannot be wildcard/group-reference syntax.
- S02-06 — `devtool detect` reports nested non-.NET roots/manifests and root counts instead of only root-level manifests.
- S02-07 — explicit target runners cannot be `auto`/`unknown`; only project-level detection may be ambiguous.
- S02-08 — workspace group names are strict identifiers.
- S02-09 — workspace groups reject names that collide with targets or duplicate after trimming.
- S02-10 — target and top-level variants validate their table shapes and report unknown selected variants clearly.
- S02-11 — target runner option subtables and target env/metadata must be TOML tables, so malformed values fail at config load.
- S02-12 — toolchain preflight rejects non-executable paths and directories instead of marking any existing path as ready.
- S02-R2-01 — `detect_repo` and kind detection now use the same ignored-directory/project-root model.
- S02-R2-02 — Gradle root discovery no longer drops an independent nested build under a parent build.
- S02-R2-03 — Android detection covers direct plugin ids, aliases, and manifests without promoting unrelated parent Gradle roots.
- S02-R2-04 — destination toolchain analysis applies the selected `mise` envelope before static preflight so chroot/SSH/PRoot plans are not based on host-only visibility.

## Validation

Focused local validation passed:

```text
247 passed
```

Command:

```bash
python3 -m pytest -q \
  tests/test_config.py \
  tests/test_global_config.py \
  tests/test_completion_state.py \
  tests/test_ui11_ui12_machine_completion_config.py \
  tests/test_mp01_mp02_target_job_workflow_contracts.py \
  tests/test_mp05_mp06_cross_target_workspace_groups.py \
  tests/test_mp07_mp08_affected_toolchain.py \
  tests/test_toolchains.py \
  tests/test_bootstrap_command.py \
  tests/test_project_runners.py \
  tests/test_ov03_config_execution_contracts.py \
  tests/test_ov04_detection_python_selection_contracts.py \
  tests/test_rem03_cli_config_discovery.py
```

## Notes for later overlays

Overlay 4 can now rely on strict target/group identities, stable config provenance, safer root-level CLI parsing, and recursive workspace discovery. Overlay 15 must still run the complete repository-wide release qualification from a clean checkout.
