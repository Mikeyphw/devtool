# Devtool 0.2.28 changes

## Android frontend 1.3.1

- Adds **Paste and import command** to the visual Run builder.
- Accepts multiline shell commands, backslash continuations, leading `cd ... &&`, environment assignments, quoted paths, and both `devtool` and `devtool-androidos`.
- Imports repository, Termux working directory, target, operation, overlay or bundle, validation, commit message, chroot, copy, Termux clipboard, live HUD, and non-interactive settings.
- Converts pasted desktop/Termux GUI HUD modes to `android-bridge`.
- Preserves unknown global and operation arguments in explicit Advanced fields instead of silently discarding them.
- Adds an exact **Paste command from clipboard** action to the raw command runner.
- Uses `DEVTOOL_HUD_SINGLETON=kill` and the Termux clipboard route as visual-builder defaults.
- Keeps a pasted leading `cd` as the Termux working directory rather than redundantly embedding it in the generated command.

## Validation

- 228 Python tests passed.
- Pure-Java importer tests compile and execute against representative direct-Termux, chroot, desktop-HUD, unknown-flag, quoted-path, and custom-subcommand commands.
- All 21 Android Java sources pass parser validation.
