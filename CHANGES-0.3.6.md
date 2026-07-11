# Devtool 0.3.6

## Live HUD step progress

- Fixed desktop and Android step bars remaining idle until a command completed.
- Normalizes target-scoped phase names such as `build[s3atelier]`, `restore[s3pi-base]`, and `test[s3atelier]`.
- Forwards each parsed .NET project/test unit to the HUD immediately with current, total, percent, warning, and error counts.
- Publishes the validation phase plan before execution so the complete operation sequence is visible.

## Multi-target and monorepo display

- Preserves independent restore/build/test segments for every artifact workspace target.
- Shows compact workspace labels instead of collapsing all targets into a single build or test segment.
- Displays the number and names of active targets in the desktop and Android context.
- Wraps desktop and Android pipelines into rows of four for readability.

## Versions

- Devtool: 0.3.6
- Android HUD: 1.5.5
