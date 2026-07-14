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
Runner cleanup happens on success, failure, and interruption: .NET build servers
are shut down and Gradle daemons are stopped.

## Failure evidence

Before rollback, devtool copies available logs and structured report files to:

```text
~/.local/state/devtool/evidence/<run-id>/
```

`evidence.json` records the copied source and destination paths. This keeps test,
lint, and package diagnostics available even when the failed overlay added or
modified report files inside the repository.
