# FS01 + FS02 compatibility and documentation consolidation report

## Scope

FS01 performs the release-readiness compatibility audit after MP01–MP10 and UI01–UI12. FS02 consolidates canonical documentation/examples so new users are taught one modern interface while older repositories, scripts, integrations, and companions keep intentional compatibility paths.

## Compatibility contract

`devtool.compatibility` defines a versioned registry for every retained compatibility boundary audited in this pass: project/target runner aliases, workspace default target, UI confirmation booleans, CLI/environment aliases, GTK HUD naming, the `DotnetTarget` Python alias, machine/IPC/Tasker/Android fallbacks, legacy artifact manifests/bundles/history, validation-session schemas, transaction recovery markers, and ordered workflow lists.

`devtool compatibility|compat` exposes that registry in text/JSON, supports category and active-only filtering, and provides `--strict` as a release gate that fails only when the selected config still requires migration.

## Migration coverage

Config migration schema v2 operates on raw TOML before normal model resolution. It safely migrates:

- `[project].kind` -> `[project].runner + platform`;
- `[targets.*].kind/type` -> `[targets.*].runner + platform`;
- `[project].default_target` -> `[workspace].default_target`;
- legacy `[ui].confirm_destructive` and `[ui].confirm_cancel` -> `[ui.confirmations]`.

`config migrate --global` covers user UI preferences. Writes create `.bak` backups. Conflicting legacy/canonical workspace defaults are reported as unsafe and `--write` refuses to guess. Devtool's own release `.devtool.toml` is canonicalized too, so `devtool compatibility --strict` and `devtool config migrate --check` pass on the repository being released.

## Canonical command generation

`--runner` and `DEVTOOL_PROJECT_RUNNER` are canonical. `--project-type` and `DEVTOOL_PROJECT_KIND` remain supported aliases. Detection publishes canonical `runner*` fields alongside legacy `kind*` fields.

Desktop command generation, the validation wizard, SSH/chroot forwarding, and the Android companion command builder now generate/forward `--runner`. The Android importer and CLI parser still accept `--project-type` for older saved commands and scripts.

## Documentation and examples

Canonical configuration/examples are consolidated in:

- `docs/BACKWARD_COMPATIBILITY.md`;
- `docs/MIGRATION_1.1.md`;
- `docs/EXAMPLES.md`;
- `examples/python-service.devtool.toml`;
- `examples/polyglot-workspace.devtool.toml`;
- `examples/ui-profile.devtool.toml`;
- `examples/release-validation.devtool.toml`.

Python, runner, Android, polyglot, validation, UI preference, configuration, architecture, README, CHANGELOG, manpage, and release-checklist material now teach canonical runner/platform/workspace/UI syntax. Legacy syntax is retained only where it documents a compatibility or migration boundary.

## Validation

The final focused cumulative selector inventory is the prior UI11/UI12 55-suite contract plus `tests/test_fs01_fs02_compatibility_docs.py`, for 56 explicit pytest selectors with defaults disabled. The compatibility registry contains 24 intentional entries after the persisted-format audit, and Devtool's release `.devtool.toml` reports zero active migrations.

To avoid the local combined-command ceiling, it was run as two non-overlapping deterministic groups:

- group 1: 708 tests + 55 subtests passed;
- group 2: 205 tests passed;
- total: **913 tests + 55 subtests passed, 0 failures**.

Additional focused runs covered canonical internal command generation, raw-TOML unsafe migration, strict compatibility gating, chroot/SSH forwarding, desktop command generation, validation wizard behavior, release documentation, completion/manpage contracts, and config migration behavior.

Static checks: `git diff --check` passes, changed Python modules compile, and generated/static zsh completion are byte-identical.

Packaging proof uses the pre-FS Devtool against a detached post-UI11/UI12 base: strict artifact validation passes, atomic apply completes with zero diagnostics, all 38 payload files match the implementation tree byte-for-byte, and the applied tree passes the dedicated FS01/FS02 suite (18 tests + 10 subtests) plus `compatibility --strict`/`config migrate --check` with zero active migrations.

The overlay manifest embeds the full 56-selector TS00 gate and declares both the `devtool` and `android_hud` workspaces because this pass also canonicalizes Android companion command generation/import.
