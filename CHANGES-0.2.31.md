# Devtool 0.2.31

- Fix Termux `RUN_COMMAND` working-directory failures when the Android frontend receives `~`, `$HOME`, `${HOME}`, relative paths, or the legacy malformed `/$HOME/...` form.
- Normalize frontend work directories to the absolute Termux home path before dispatch and before recording run history/HUD state.
- Add regression coverage for imported shell commands using `$HOME/Code/...`.
