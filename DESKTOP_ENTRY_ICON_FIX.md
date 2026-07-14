# Desktop launcher and icon fix

The desktop package and runtime HUD identity no longer depend on the interactive shell PATH.

## Control Center package

- Uses `/usr/bin/devtool-control-center` in both `TryExec` and `Exec`.
- Uses the stable `devtool-control-center` WM class.
- Installs SVG and 64/128/256 px PNG icons.
- Installs a pixmaps fallback.
- Refreshes the desktop database and hicolor icon cache in Debian `postinst`/`postrm` scripts.
- Verifies the desktop entry and raster icon are present in the generated package.

## Generated HUD identity

- Resolves the actual `devtool` executable to an absolute path.
- Falls back to the active Python interpreter with `-m devtool`.
- Writes the absolute icon path into the generated desktop entry.
- Refreshes the per-user desktop database when available.

## Validation

- 465 tests passed.
- 28 subtests passed.
- Python compilation passed.
- Shell syntax checks passed.
- Raster icon dimensions validated.
