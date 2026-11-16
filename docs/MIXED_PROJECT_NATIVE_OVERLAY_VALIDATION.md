# Native overlay inspection in mixed repositories (Devtool v3.1)

Native `validate overlay` / `validate overlays` inspect immutable artifacts and
compare their manifest, inventory, checksums and preimages. They do **not** run a
project toolchain, so no Android/Rust/Make runner should be inferred merely to
perform archive inspection. A native `apply overlay --static-validate` similarly
runs the overlay validator and declared static checks only.

This change uses the existing `load_project_config(allow_ambiguous_project_kind=True)`
**only** for archive-only validation and static-only overlay application. It does
not select an Android/Rust runner, skip artifact checks, or allow ambiguous
`build`, `test` or ordinary project `validate` execution.

Regression coverage: `tests/test_native_overlay_mixed_repo_runner.py` checks both
native validation forms, static versus normal apply, strict real-execution
refusal, archive error propagation, and explicit typed runner selection.

Source: `Mikeyphw/devtool` main `c3f766e9252391779a854bb4bcfd47cef077aefe`.
This is a general Devtool compatibility fix, not a BakaSU overlay. Once merged,
regenerate BakaSU F07 against the new reviewed Devtool main commit and rerun
its unchanged native package/check/apply workflow. Never replay an applied F07
package or weaken the required GitHub checks.
