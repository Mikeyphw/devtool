# Devtool pyenv Addition Audit — 2026-09-28

## Decision

pyenv is added as a first-class **Python runtime provider** inside M004 ENV. It is not a Python dependency/environment manager, it never owns non-Python targets, and it does not create a new program or overlay. The canonical campaign remains 63 overlays.

## Upstream semantics incorporated

The provider model follows pyenv's documented behavior:

- `PYENV_VERSION` overrides local version files;
- `.python-version` is the normal per-project version-selection file;
- legacy `.pyenv-version` remains readable for backward compatibility when no `.python-version` wins;
- `PYENV_DIR` changes where pyenv starts searching for version files;
- `PYENV_ROOT` identifies pyenv's installation/version root;
- `pyenv version-name` / `pyenv version` expose the active selection and its source;
- `pyenv which <command>` identifies the real executable selected by pyenv;
- `pyenv exec <command> ...` executes under that selection;
- `system` is pass-through to the system executable rather than a pyenv-owned runtime;
- ordinary Devtool execution never performs `pyenv install`, `uninstall`, `rehash`, `local`, `global`, or `shell` as hidden setup.

References:
- https://github.com/pyenv/pyenv/blob/master/README.md
- https://github.com/pyenv/pyenv/blob/master/COMMANDS.md

## Ownership and precedence

- pyenv auto-selection is Python-only and requires a pyenv-owned project marker;
- explicit `toolchain_manager = "pyenv"` is rejected for non-Python targets;
- a shared `.tool-versions` file remains asdf-owned in auto mode when asdf is available;
- dedicated mise configuration still wins mise auto-ownership;
- pyenv owns only Python runtime selection; Poetry/PDM/Pipenv/Hatch/venv/uv remain language-environment/dependency owners;
- `system` pass-through leaves the runtime provider as system while preserving pyenv as a composition provider.

## Qualification / trust requirements

`PyenvQualification` records:

- pyenv executable identity and version;
- selected version-file path and digest;
- requested versions and active versions independently;
- `PYENV_VERSION` override;
- `PYENV_DIR` search root;
- version-source path and digest where available;
- `PYENV_ROOT` identity;
- actual `pyenv which python` / `python3` results;
- language-manager executables resolved through pyenv when relevant.

Qualification fails closed when:

- pyenv claims a non-system Python runtime but cannot resolve `python`;
- requested and active versions disagree;
- the pyenv executable disappears, moves, or is replaced after qualification;
- direnv attempts to silently override `PYENV_VERSION` while pyenv owns runtime selection.

## Canonical roadmap extension

The following merged ENV promises are appended without changing overlay order or count:

- `ENV-P117` — pyenv is a first-class Python runtime provider and can never own non-Python targets.
- `ENV-P118` — pyenv selection provenance includes `PYENV_VERSION`, `.python-version`, legacy `.pyenv-version`, `PYENV_DIR`, and the observed active source.
- `ENV-P119` — `PyenvQualification` binds provider executable/version, version-file/source digests, requested/active versions, resolved Python tools, override state and root/search identities.
- `ENV-P120` — ordinary execution qualifies pyenv read-only and uses `pyenv exec`; it never performs hidden install/uninstall/rehash/version mutation.
- `ENV-P121` — Python runtime-provider ownership remains separate from Poetry/PDM/Pipenv/Hatch/venv/uv environment/dependency ownership.
- `ENV-P122` — pyenv executable disappearance, PATH drift or in-place replacement fails closed.
- `ENV-P123` — direnv cannot silently replace pyenv-owned `PYENV_VERSION`; such conflict fails before execution.
- `ENV-P124` — pyenv `system` pass-through does not falsely claim the actual Python runtime as pyenv-owned.
- `ENV-P125` — destination-returned pyenv runtime snapshots and destination `PYENV_ROOT` ownership are completed in ENV-X04.
- `ENV-P126` — ENV-G1 proves fresh-process pyenv ownership, pass-through, pyenv+Poetry, conflict and identity fixtures.
- `ENV-P127` — ENV-G3 closes the local/Termux/chroot/PRoot/SSH pyenv matrix and final source-obligation disposition.

## Gate requirements added

ENV-G1 must prove at minimum:

1. Python `.python-version` + qualified pyenv selects pyenv.
2. The same marker cannot make pyenv own Go/other targets.
3. `PYENV_VERSION` precedence is provenance-bearing.
4. `PYENV_DIR`/legacy-file selection is represented in the receipt.
5. pyenv + Poetry preserves separate runtime vs environment-manager identity.
6. `system` pass-through records system runtime truth.
7. missing/mutated pyenv fails closed.
8. direnv cannot override pyenv runtime selection silently.
