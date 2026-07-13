# Devtool desktop HUD, clipboard, repositories, and artifact-safety patch

## Desktop HUD

- Makes the outer HUD substantially more transparent on both dark and light backgrounds.
- Keeps workspace cards and recessed surfaces readable with stronger outlines.
- Retains saved HUD positioning and multi-monitor safety.
- Applies the preferred installed Nerd Font as the default font for both the Control Center and compact HUD.

## Clipboard command import

- Preserves the full shell prelude, including `cd ... && \\`, `pushd`, environment assignments, exports, and sourced shell files.
- Renames the form action to **Paste from clipboard**.
- Fills repository, artifact, operation, target, validation, commit, and keep-artifact fields.
- Shows the preserved original shell command inline.
- Runs form-generated workflows through the preferred login shell, using zsh first, with an explicit repository `cd`.

## Artifact verification

- Distinguishes file existence, missing manifest, malformed JSON, and semantic validation failure.
- Reports the exact embedded or sidecar manifest source.
- Prevents the Run button from starting an apply until the artifact and manifest pass inline validation.

## Repositories

- Saved repositories remain opt-in.
- Active runs from unsaved repositories appear temporarily as **LIVE · NOT SAVED** entries.
- Live-only entries disappear after their runs finish unless the user explicitly saves them.
- Keeps repository activity/history, active runs, full Git operations, and commit-to-artifact links.

## Artifact deletion safety

- Source artifacts and sidecars are now preserved after every failed apply, including failures before extraction.
- Successful applies still remove artifacts by default.
- `--delete-artifact-on-failure` is now an explicit opt-in.
- `--keep-artifact` continues to preserve artifacts after success.

## Validation

- 320 tests passed.
- 13 subtests passed.
- Python byte-compilation and shell syntax checks passed.
