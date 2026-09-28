# Devtool asdf Addition Audit — 2026-09-28

## Decision

asdf is added as a first-class M004 ENV tool/runtime provider, not as a generic shell wrapper and not as a new program. The canonical campaign remains 63 overlays.

## External behavior incorporated into the contract

- `.tool-versions` is an asdf version-selection surface and can be overridden by `ASDF_<TOOL>_VERSION`.
- `ASDF_TOOL_VERSIONS_FILENAME` can select a different version filename.
- `ASDF_DIR` and `ASDF_DATA_DIR` affect asdf core/data ownership and must be absolute when explicitly set.
- `.asdfrc` / `ASDF_CONFIG_FILE` control machine-specific behavior, including `legacy_version_file`.
- `asdf exec <command> ...` executes within the currently selected tool environment.
- plugin origin can be inspected through `asdf plugin list --urls`.
- ordinary Devtool execution must not run mutating asdf operations such as `install`, `plugin add/update`, or `reshim` as hidden setup.

Official references consulted: https://asdf-vm.com/manage/versions.html, https://asdf-vm.com/manage/configuration.html, https://asdf-vm.com/manage/core.html, https://asdf-vm.com/manage/plugins.html.

## Ownership and precedence

- dedicated mise config (`mise.toml` / `.mise.toml`) wins mise auto-ownership;
- the shared `.tool-versions` surface selects asdf in auto mode when asdf is available;
- explicit `toolchain.manager` remains authoritative;
- Python-only `.python-version`/pyenv delegation remains a separate provider concern and cannot make pyenv own non-Python targets;
- only one toolchain-provider role may be active in a composition plan.

## Security / trust rules

- no hidden install, plugin mutation, trust mutation or reshim;
- provider executable identity is captured before execution and reverified after PATH-changing overlays;
- provider disappearance or same-path replacement fails closed;
- plugin remote credentials, query strings and fragments are never persisted;
- `ASDF_*_VERSION` overrides are provenance-bearing and conflict with a direnv overlay attempting to silently replace asdf-owned tool versions;
- explicit `ASDF_CONFIG_FILE`, `ASDF_DIR`, and `ASDF_DATA_DIR` must be absolute;
- explicit environment snapshots do not inherit ambient controller HOME;
- custom tool-version filename is validated as a filename, not a traversal path;
- requested versions are not reported as resolved unless `asdf current` proves them.

## Canonical roadmap extension

New merged ENV promises are allocated without changing overlay count:

- ENV-P105 — asdf is a first-class tool/runtime provider within EnvironmentCompositionPlan.
- ENV-P106 — `.tool-versions`/custom version-file ownership is deterministic and never silently reinterpreted as mise ownership.
- ENV-P107 — AsdfQualification binds provider identity/version, version-file digests, exact current versions, plugin provenance, config/legacy policy, overrides and data-dir identity.
- ENV-P108 — ordinary execution uses qualified `asdf exec` and never performs hidden install/plugin/reshim mutation.
- ENV-P109 — asdf provider disappearance, PATH drift or in-place replacement fails closed.
- ENV-P110 — `ASDF_*_VERSION` overrides are provenance-bearing and direnv cannot silently replace provider-owned versions.
- ENV-P111 — plugin-origin evidence is secret-safe.
- ENV-P112 — custom version filename and ASDF_CONFIG_FILE policy participate in qualification.
- ENV-P113 — destination asdf state, legacy-version-file resolution and remote ASDF_DATA_DIR ownership are completed in ENV-X04.
- ENV-P114 — asdf composes with Python/Poetry/Go without taking ownership of language-environment or scheduling roles.
- ENV-P115 — ENV-G1 proves fresh-process asdf ownership/identity/no-hidden-mutation fixtures.
- ENV-P116 — ENV-G3 closes the cross-substrate asdf matrix and source-obligation disposition.

## Audit-loop amendments

Later ENV-X02 audit passes added three required distinctions:

- `asdf current <plugin>` output is parsed in both version-first and plugin+version forms; requested text is never substituted for missing current evidence.
- plugin identity and executable identity are distinct (`golang` plugin → `go` executable), and both remain represented in evidence.
- the asdf `system` version is pass-through: asdf remains a composition provider but does not falsely claim the actual Python/Go runtime.

These are covered by cross-provider promises `ENV-P128`–`ENV-P131` in `ENV_X02_PROVIDER_PLAN_AMENDMENT_20260928.md`.


## Final audit-loop amendment

`ASDF_<TOOL>_VERSION` is not identity-only metadata. It participates in canonical selected-tool truth even when no `.tool-versions`/custom version file exists, takes precedence over the corresponding file request, and must still be corroborated by `asdf current` before local runtime ownership is claimed. This closes the final ENV-P110 / cross-provider ownership gap found by the bounded post-v2 audit.
