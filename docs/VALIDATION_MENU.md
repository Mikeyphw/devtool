# Interactive validation cockpit

The interactive cockpit is a validation-only frontend over Devtool's existing
CLI, durable session engine, and API-v1 status/plan controls.

```bash
devtool -r /path/to/repo menu validation
devtool -r /path/to/repo validation-menu
devtool -r /path/to/repo vm
```

It prefers `fzf` and uses `bat` for previews. `--no-fzf` forces a numbered
terminal fallback, so the feature remains usable in minimal shells and recovery
environments.

## Design contract

The cockpit does not duplicate validation execution. It loads resolved project
configuration, reads the current durable session and plan, creates a
`ValidationDraft`, validates incompatible option combinations, and compiles a
normal `argv` list. Shell text exists only for display and clipboard use.

Session controls and every continuation/retry command use the session revision
returned by the status API. Stale views therefore fail safely instead of
mutating or advancing newer state. Ctrl-R and the numbered fallback's explicit
Refresh action rebuild project context, durable status, plan, events, and task
catalogs rather than merely repainting cached text.

## Landing screen

The first actions change according to session state. Depending on availability,
the screen can offer:

- start a durable session;
- run the next phase, selected phases plus dependency closure, every phase
  through a checkpoint, or every remaining phase;
- retry a blocked phase, including the recommended safe resource profile;
- pause after the active phase, cancel a pending pause, or stop immediately;
- recover an interrupted owner or journaled rollback;
- inspect status, plan, events, logs, and the durable report;
- reset a phase and its dependents, reset from a checkpoint, or reset all;
- accept incompatible plan drift with an audit reason;
- finalize a completed session;
- abort while explicitly keeping changes or performing rollback.

Mutating controls are hidden or disabled when the current session does not
permit them.

## Guided wizard

The wizard pages are method, repository context, pipeline coverage, phase scope,
test selection, resources, runtime guards, failure policy, and output.

Supported validation methods:

- smart one-shot validation;
- collect-all validation;
- start or continue a durable sequential session;
- watch mode;
- runtime, target, or variant matrices;
- Wine validation;
- overlay integrity and bundle integrity;
- apply-time immediate, sequential, deferred, static, validation-only, or
  manifest-validation-disabled workflows.

Phase choices always come from the selected runner's resolved validation plan,
even before a durable session exists. Multi-selection previews show the combined
prerequisite closure and already-satisfied phases. Exact `--phase-only`
execution is offered only when prerequisites are complete.

Every existing structured test selector is represented: saved profile, generic
selectors, exact IDs, tags, excluded tags, paths, classes, methods, native
filter, Gradle task/script override, failed tests, changed tests, Git base, and
Android instrumentation. Gradle tasks are gathered from configuration and build
files. `--runtime-tasks` additionally runs `gradlew tasks --all -q` once when the
cockpit opens.

## Resource policy

The wizard includes `automatic`, `android-low-power`, and `standard` profiles,
plus one-invocation overrides:

```text
--max-workers
--cpu-limit
--gradle-heap-mb
--memory-guard-mb
--low-priority / --normal-priority
--parallel / --no-parallel
--build-cache / --no-build-cache
--daemon / --no-daemon
```

Explicit overrides are applied after the selected profile. Durable sessions
store them in session options so a later continuation preserves the intended
policy unless the user explicitly changes it.

The same page exposes daemon lifecycle, bounded infrastructure and resource
retries, and manual-only retry mode. Runtime guards cover battery percentage,
charging state, temperature, free disk, invocation duration, maximum phase
count, and incomplete-checkpoint exit behavior.

## Review and execution

The review screen shows a plain-language summary, warnings, and the exact
canonical command. Available actions are run, dry-run, copy, edit, save recipe,
and render with `bat`. Edited command text is parsed with `shlex`; Devtool still
executes an argument vector rather than passing the text to a shell.

## Recipes

Built-in and user recipes appear in the same menu. User recipes are TOML files:

```text
~/.config/devtool/validation-recipes.toml
REPOSITORY/.devtool/validation-recipes.toml
```

Repository recipes override a global or built-in recipe with the same key. A recipe stores command arguments and an optional argument-vector prefix for
explicit repository, target, variant, execution environment, HUD, clipboard,
and output choices. It never stores shell syntax and can be reviewed or edited
before execution.

Noninteractive recipe expansion is useful for scripts:

```bash
devtool -r /path/to/repo vm --recipe smart --print-command
devtool -r /path/to/repo vm \
  --recipe overlay-integrity \
  --artifact /path/to/overlay.zip \
  --print-command
devtool -r /path/to/repo vm \
  --recipe target-matrix \
  --matrix-values app,android_hud \
  --print-command
```

## Keys

| Key | Action |
| --- | --- |
| Enter | Select, continue, or run the highlighted action |
| Tab / Shift-Tab | Add or remove multi-selection entries |
| Ctrl-R | Reload repository context, session status, revision, plan, events, and tasks |
| Ctrl-P | Toggle the preview pane |
| Ctrl-D | Dry-run the reviewed command |
| Ctrl-Y | Copy the canonical command |
| Ctrl-E | Edit the command before execution |
| Ctrl-S | Save the command as a recipe |
| Ctrl-L | Open the latest Devtool log with `bat` |
| Escape | Return to the previous page or cancel |

## Safety behavior

The wizard rejects conflicting phase scopes, incomplete matrix or artifact
inputs, unsafe `--phase-only` choices, deferred-apply phase selection,
contradictory daemon choices, and session controls without required context.
Abort and plan-drift acceptance remain explicit audited actions. The default
sequential failure policy is to pause and preserve the patched tree plus its
rollback point.

## Stable JSON and compatibility names

For durable runs and validation controls, the output page can emit either normal
human output or the stable API-v1 JSON envelope in pretty or compact form. The
compiler emits `--json` and `--machine pretty|compact` as appropriate.

Use `--resource-profile` for resource policy and `--test-profile` for structured
test selection. Validation's historical `--profile` resource alias remains
accepted for compatibility but emits a deprecation warning because it is easily
confused with test profiles.
