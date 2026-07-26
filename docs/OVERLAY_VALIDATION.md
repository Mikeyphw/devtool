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
    "rollback_on_failure": true
  }
}
```

Explicit CLI or frontend choices take precedence. The same defaults work on a
single overlay, a bundle, and each bundle entry.

## Transaction sequence

A validating apply follows this sequence:

1. create a transaction backup;
2. apply the artifact;
3. reload any target configuration introduced by the artifact;
4. select the runner and execution environment;
5. restore, build, test, lint, package, and verify according to the validation
   profile and runner capabilities;
6. execute the artifact validator, when present;
7. commit only after all required checks pass;
8. roll back repository files when a required check fails.

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

A real validation failure is terminal for an attached apply transaction. When
rollback-on-failure is enabled, devtool preserves evidence, restores the backup,
marks every unrun phase `cancelled`, records the session as
`failed-rolled-back`, and clears the active validation and rollback markers. A
rollback failure records `rollback-failed` and keeps the session active only so
`validate abort` can retry repository recovery; validation cannot continue.
Interruptions remain resumable and do not use this failure path.

## Failure evidence

Before rollback, devtool copies available logs and structured report files to:

```text
~/.local/state/devtool/evidence/<run-id>/
```

`evidence.json` records the copied source and destination paths. This keeps test,
lint, and package diagnostics available even when the failed overlay added or
modified report files inside the repository.
