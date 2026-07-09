# Devtool 0.2.26 changes

## Desktop HUD

- Publishes the new run identity before starting or reusing the singleton HUD, preventing a previous completed run from flashing before the current run begins.
- Adds regression coverage for stale global HUD state.

## Overlay bundles

- Detects wrapper archives whose payload consists entirely of child overlay archives.
- Applies child overlays sequentially in deterministic filename order instead of copying their ZIP files into the target repository.
- Preserves normal behavior for overlays that contain source files plus reference archives.

## Android companion 1.2.0

- Native Tasker/Locale action plug-in with synchronous completion and result variables.
- Direct Termux `RUN_COMMAND` execution with stdout, stderr, exit code and internal error handling.
- Private per-install Tasker action authorization token to prevent command-proxy abuse.
- Modern dark, flat, borderless UI using the desktop GTK palette.
- Optional Android Monet colors on Android 12 or newer.
- Android launcher and notification artwork matching the GTK HUD icon.
- User-initiated overlay recovery path compatible with modern Android foreground-service restrictions.
- Operation-aware completion titles and app settings for command, workdir, execution mode and overlay behavior.

## Repository hygiene

- Adds repository-wide ignore rules for Gradle/Python caches, build outputs, APKs, local secrets, runtime state and editor files.
- Source distribution excludes stale generated Android artifacts and old debug APKs.
