# Devtool 0.3.0

- Added self-contained artifact schema v2 with repository, monorepo workspace, target, solution, inventory, apply, commit, and validation policies.
- Added SHA-256 and size verification for payloads plus source hashes for modify/delete actions.
- Added inventory-backed deletes while retaining `.devtool-delete` compatibility.
- Added atomic bundle execution with dependency ordering, optional overlays, per-target validation, and one final commit.
- Added disposable artifact and bundle validation scripts under `.devtool-artifact/`, with `$TMPDIR` preference, timeout handling, captured logs, and rollback integration.
- Added artifact-driven target, validation, and commit defaults with CLI precedence and `--no-validate`, `--no-commit`, and `--no-artifact-script` opt-outs.
- Added richer bundle creation flags and artifact manifest rendering.
