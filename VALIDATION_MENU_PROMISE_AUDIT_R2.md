# Validation menu promise audit, corrected release

This audit checks the corrected implementation against the validation cockpit
and wizard scope originally proposed.

## Result

All promised functional areas are now implemented. The correction also fixes a
packaged `vm` alias defect and duplicate `--hud-overlay` generation discovered
during installed-wheel verification.

## Safety and state

- [x] Active-session continue, retry, next, through, selected, exact, and
  remaining commands carry `--session` and `--if-revision`.
- [x] Pause, stop, recover, reset, plan acceptance, finalize, and abort remain
  revision guarded.
- [x] Ctrl-R rebuilds parsed repository context, durable status, revision, plan,
  events, and tasks.
- [x] The numbered fallback exposes an explicit refresh action.
- [x] Plan-drift acceptance requires an audit reason.
- [x] Reset supports one phase, reset from a phase, and reset all.

## Methods and scope

- [x] Smart one-shot, collect-all, durable start/continue, watch, matrix, Wine,
  overlay, bundle, and apply-time validation.
- [x] Next, remaining, through, selected plus prerequisites, exact phase,
  from-phase, maximum phases, runtime budget, and pause-after boundaries.
- [x] Pre-session phase menus are derived from the actual selected runner plan;
  no hardcoded Android/generic fallback remains.
- [x] Multi-selection displays the combined dependency closure and already
  satisfied phases.

## Selection, resources, and output

- [x] Every structured test selector and Gradle task discovery path is exposed.
- [x] Automatic, Android low-power, standard, and custom one-run resource
  policies are available.
- [x] Device/runtime guards and failure policies are available.
- [x] Stable validation JSON can be selected in pretty or compact form.
- [x] Review supports run, dry-run, copy, edit, save, bat rendering, and latest
  log opening.
- [x] Validation `--profile` remains compatible but warns; the preferred names
  are `--resource-profile` and `--test-profile`.

## Recipes and interfaces

- [x] Global and repository TOML recipes preserve explicit repository, target,
  variant, environment, HUD, clipboard, performance, and output arguments.
- [x] `devtool menu validation`, `devtool validation-menu`, `devtool vm`, and
  `devtool validate wizard` normalize correctly.
- [x] fzf and complete numbered fallback flows are covered.
- [x] Documentation, manual, completion, full command parser round-trips, a
  pseudo-terminal guided wizard, and Termux/chroot generation are covered.

## Verification

- Focused cockpit/session/CLI gate: **181 passed, 30 subtests passed**.
- Wider validation release gate: **244 passed, 30 subtests passed**.
- Wheel build: passed.
- Isolated installed-wheel `devtool vm` smoke test: passed.
- Isolated revision-guard and stable-JSON command smoke test: passed.
