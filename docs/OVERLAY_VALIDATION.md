# Runner-independent overlay validation

Overlay and snapshot validation use the selected project runner. The apply path
no longer assumes a .NET solution.

## Artifact defaults

Schema-v2 artifacts may declare language-neutral validation defaults:

```json
{
  "schema_version": 2,
  "type": "overlay",
  "target": "android_app",
  "validation": {
    "required": true,
    "test_profile": "quick",
    "execution_environment": "native-termux",
    "failure_action": "pause"
  }
}
```

Explicit CLI or frontend choices take precedence. The same defaults work on a
single overlay, a bundle, and each bundle entry.

Artifacts may also declare an exclusive Gradle task list:

```json
{
  "validation": {
    "required": true,
    "tasks": [
      ":app:compileDebugKotlin",
      ":app:testDebugUnitTest",
      ":app:lintDebug"
    ]
  }
}
```

`validation.tasks` is equivalent to passing repeated `--task` flags during
validation: devtool runs exactly those native Android/Gradle tasks and does not
append its built-in restore/build/test/lint/package phases. The aliases
`validation.gradle_tasks`, `validation.test_tasks`, `validation.test_task`, and
`validation.task` are accepted for compatibility, but new artifacts should write
`validation.tasks`.

CLI flags still win:

- `--no-validate` disables manifest-requested validation and ignores manifest tasks.
- `--validate` enables validation even when the manifest says `mode=disabled`.
- `--task` / `--test-task` replaces `validation.tasks`.

Overlay bundles may declare tasks at the bundle level:

```json
{
  "type": "overlay_bundle",
  "validation": {
    "required": true,
    "strategy": "both",
    "tasks": [":app:compileDebugKotlin", ":app:lintDebug"]
  }
}
```

A bundle entry may also override tasks for one nested overlay:

```json
{
  "apply_order": [
    {
      "path": "overlays/android.zip",
      "target": "dustui_android",
      "validation": {"tasks": [":app:testDebugUnitTest"]}
    }
  ]
}
```

## Transaction sequence

A validating apply follows this sequence:

1. create a transaction backup;
2. apply the artifact;
3. reload any target configuration introduced by the artifact;
4. select the runner and execution environment;
5. restore, build, test, lint, package, and verify according to the validation
   profile and runner capabilities;
6. execute the artifact validator, when present;
7. block and retain the applied tree when a required validation check fails;
8. commit only after all required checks pass;
9. roll back only when the user or artifact explicitly selects the rollback failure action.

Generated caches and build outputs are not used as rollback source material.
Runner cleanup happens on success, failure, and interruption according to the
selected runner policy.

## Paused Android validation transactions

An Android/Gradle apply may opt into the same durable phase engine:

```bash
devtool -r "$HOME/Code/app" apply-overlay patch.zip --validate --sequential --through compile
devtool -r "$HOME/Code/app" validate resume --through package
devtool -r "$HOME/Code/app" validate resume --complete
```

While paused, the repository transaction remains in
`applied-pending-validation` or `partially-validated` state. The backup and
artifact are retained, no Git commit is created, and notifications say
"validation paused" rather than "applied". An unrelated devtool command refuses
to consume or roll back that intentional checkpoint. `validate abort` restores
the transaction by default; `validate abort --keep-changes` abandons the session
without restoring files. Finalization runs artifact validators and expectations,
creates the requested commit, marks the transaction complete, records history,
and only then performs artifact cleanup.

A real validation failure now blocks the session instead of destroying the applied tree. The failed phase is cached, later phases remain pending, validation evidence is preserved, and the transaction enters `validation-blocked`. Fix the problem and run `devtool validate continue --retry-failed`, or explicitly choose another outcome.

Failure actions are:

- `pause`, the default for new deferred/sequential sessions, retains both changes and rollback data;
- `rollback`, selected with `--failure-action rollback` or legacy explicit artifact policy, runs the verified rollback journal;
- `keep`, closes the transaction and keeps changes without a successful validation verdict;
- `prompt`, reserved for interactive frontends and treated conservatively when no prompt is available.

Interruptions, memory/resource termination, environment loss, and timeouts become retryable interrupted attempts rather than code failures. A Git commit failure after all required phases pass produces `validated-uncommitted`; it does not roll back validated code. `validate recover` reclaims only a dead phase owner and also resumes an incomplete journaled rollback.

## Machine-controlled deferred validation

Schema-v4 sessions expose the same transaction-safe workflow through API-v1
JSON envelopes. A frontend first reads `validate status --json`, keeps the
returned `session_id` and `revision`, and includes both on every mutation. An
old revision is rejected before phase reconciliation or transaction mutation.

```bash
devtool -r "$REPO" validate run --scope next --session "$SESSION" --if-revision "$REV" --json
devtool -r "$REPO" validate pause --session "$SESSION" --if-revision "$REV" --json
devtool -r "$REPO" validate stop --session "$SESSION" --if-revision "$REV" --json
```

Pause and stop requests are written outside the repository run lock and are
consumed by the active phase supervisor. Stop produces an interrupted, retryable
attempt. Pause lets the phase finish and pauses at the next durable boundary.
Neither request rolls back the overlay. Machine abort is deliberately explicit:
use either `--keep-changes` or `--rollback`; an ambiguous machine abort is
rejected.

The session event stream lives beside `state.json` as `events.jsonl`. It is
append-only, bounded on read, and cursor-addressable through `validate events`.
This lets Android survive process death and rebuild its validation timeline
without scraping command output.

## Dependency graph and minimum checkpoints

Durable validation sessions store an explicit phase dependency graph. Selecting
`--phase package` includes unmet prerequisites; `--phase-only package` refuses
to run until its declared prerequisites are complete. Artifact
`validation.minimum_phase` is resolved when the session is created and checked
again before finalization, so plan drift cannot silently remove the required
gate.

Required phase removal, graph reordering, or phase identity/scope changes enter
`recovery-required`. Restart the session, or accept a reviewed change with an
audited reason:

```bash
devtool validate accept-plan-change --reason "Reviewed updated target plan"
```

Configuration, source, resources, and tests have independent input
fingerprints. Changed domains invalidate their consumers and graph dependents.
A phase that exits successfully after relevant inputs changed during execution
is marked stale and rerun instead of blessing an ambiguous snapshot.

## Failure evidence

Before rollback, devtool copies available logs and structured report files to:

```text
~/.local/state/devtool/evidence/<run-id>/
```

`evidence.json` records the copied source and destination paths. This keeps test,
lint, and package diagnostics available even when the failed overlay added or
modified report files inside the repository.
