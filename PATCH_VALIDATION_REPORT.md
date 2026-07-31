# Patch Validation Report

## Patch

Interactive validation cockpit and wizard for Devtool.

## Architecture

The new `devtool.interactive` package is a command compiler and terminal UI over
the existing validation engine. It reads resolved configuration plus durable
status/plan payloads, builds a validated `ValidationDraft`, and executes the
ordinary Devtool argv. It does not duplicate transaction, phase, retry,
recovery, report, or rollback logic.

## Validation performed

- Python syntax compilation: passed.
- Interactive validation, CLI, completion/manual, durable session, and control
  API gate: 167 passed, 30 subtests passed.
- Canonical recipe command smoke tests: passed.
- Zsh runtime completion and packaged completion equality: passed.
- Wheel build with existing local build dependencies: passed.
- Wheel contents check: all seven `devtool.interactive` package files present.
- Isolated wheel-target CLI smoke test for help and target-matrix recipe expansion: passed.
- Broader historical suite: attempted. Remaining failures are pre-existing and
  reproduce in the untouched input archive. Representative causes include
  missing historical phase/release fixtures, stale Python runner expectations,
  a non-executable fake Python binary, and Android/JDK test fixtures.

## Primary changed files

- `devtool/cli.py`
- `devtool/completion.py`
- `devtool/interactive/`
- `completions/_devtool`
- `tests/test_validation_interactive_menu.py`
- `README.md`
- `docs/VALIDATION_MENU.md`
- `share/man/man1/devtool.1`
- `PHASE_48_VALIDATION_MENU_WIZARD.md`
- `CHANGELOG.md`
