# Devtool artifact manifests (schema v2)

Devtool reads `.devtool-artifact.json`, `devtool-artifact.json`, or `artifact.devtool.json` from an overlay. Bundles may use the same manifest names or the legacy `devtool-bundle.json` / `.devtool-bundle.json` names.

All payload and inventory paths are repository-relative. Artifact metadata under `.devtool-artifact/` is never copied into the repository.

## Overlay

```json
{
  "schema_version": 2,
  "type": "overlay",
  "name": "S3OC force-close diagnostics",
  "description": "Adds crash capture, recovery reporting, and regression tests.",
  "repository": {
    "name": "sims3tools",
    "remote": "https://github.com/OWNER/sims3tools.git",
    "branch": "main"
  },
  "workspace": {
    "name": "s3oc",
    "root": "s3oc_modern"
  },
  "target": "s3oc",
  "solution": "s3oc_modern/S3oc.Modern.sln",
  "inventory": {
    "format": 1,
    "entries": [
      {
        "path": "s3oc_modern/src/S3oc.App/Diagnostics/CrashSentinel.cs",
        "action": "add",
        "sha256": "PAYLOAD_SHA256",
        "size": 12345
      },
      {
        "path": "s3oc_modern/src/S3oc.App/App.axaml.cs",
        "action": "modify",
        "source_sha256": "EXPECTED_EXISTING_FILE_SHA256",
        "sha256": "PAYLOAD_SHA256",
        "size": 6789
      },
      {
        "path": "s3oc_modern/src/S3oc.App/LegacyCrashHandler.cs",
        "action": "delete",
        "source_sha256": "EXPECTED_EXISTING_FILE_SHA256"
      }
    ]
  },
  "apply": {
    "atomic": true,
    "conflict_policy": "fail",
    "path_policy": "repository-relative",
    "requires_clean_worktree": false,
    "commit": {
      "enabled": true,
      "strategy": "single",
      "message": "Add S3OC force-close diagnostics"
    }
  },
  "validation": {
    "required": true,
    "restore": true,
    "build": true,
    "test": true,
    "rollback_on_failure": true,
    "script": {
      "path": ".devtool-artifact/validate.sh",
      "runner": "bash",
      "sha256": "VALIDATION_SCRIPT_SHA256",
      "timeout_seconds": 900
    },
    "expected": {
      "restore": "pass",
      "build": "pass",
      "tests": "pass",
      "maximum_warnings": 0
    }
  },
  "notes": [
    "Adds persistent force-close diagnostics.",
    "Does not change package parsing or export behavior."
  ]
}
```

`source_sha256` protects `modify` and `delete` actions from applying to an unexpected base file. `sha256` and `size` verify the archive payload. If an inventory is present, every payload file must be declared.

`validation.expected.restore`, `validation.expected.build`, and
`validation.expected.tests` accept common outcome names: `pass`, `passed`,
`success`, `succeeded`, `skip`, `skipped`, `warn`, `warning`, `warnings`,
`fail`, `failed`, `error`, and `errors`.

Use `--ignore-checksum` only for deliberate recovery or locally modified artifacts. It skips SHA-256 presence, format, and mismatch checks for payloads, source files, and embedded validation scripts, while retaining repository identity, path safety, inventory completeness, payload size, conflict, and validation-policy checks. Unlike `--no-manifest-check`, it does not disable the rest of artifact validation. Devtool prints a warning whenever the option is active.

Embedded validator scripts are extracted to a private temporary directory but execute with the repository root as their working directory. The temporary path remains available through `DEVTOOL_VALIDATION_TMPDIR`.

The legacy `files`, `.devtool-delete`, `repo_name`, and `validate_target` fields remain accepted.

## Monorepos

Use `workspace` for one monorepo project. Use `workspaces` for cross-project overlays. Paths remain repository-relative; workspace roots are for scope, display, and validator environment variables, not path prefixing.

## Bundles

Bundles apply all nested overlays inside one transaction and create one final commit after every required validation succeeds.

```json
{
  "schema_version": 2,
  "type": "overlay_bundle",
  "name": "S3MO and S3OC force-close diagnostics",
  "repository": {"name": "sims3tools"},
  "overlays": [
    {
      "id": "s3mo-diagnostics",
      "artifact": "overlays/s3mo.zip",
      "target": "s3mo_s3mo_s3mo",
      "required": true
    },
    {
      "id": "s3oc-diagnostics",
      "artifact": "overlays/s3oc.zip",
      "target": "s3oc",
      "required": true,
      "after": ["s3mo-diagnostics"],
      "requires": ["s3mo-diagnostics"]
    }
  ],
  "apply": {
    "atomic": true,
    "conflict_policy": "fail",
    "commit": {
      "enabled": true,
      "strategy": "single",
      "message": "Add S3MO and S3OC force-close diagnostics"
    }
  },
  "validation": {
    "required": true,
    "strategy": "per-overlay",
    "rollback_on_failure": true,
    "script": {
      "path": ".devtool-artifact/validate-bundle.sh",
      "runner": "bash",
      "sha256": "BUNDLE_SCRIPT_SHA256",
      "timeout_seconds": 1200
    }
  }
}
```

`after` controls stable topological ordering. `requires` additionally means the dependency must be present. Dependency cycles are rejected before changes are made. Missing entries are allowed only when `required` is false and no present overlay requires them.

Validation strategies are `per-overlay`, `after-all`, and `both`. Atomic bundles only support a single final commit.

## Disposable validation scripts

Scripts must live under `.devtool-artifact/`, have a declared SHA-256, and use a supported runner: `bash`, `sh`, or `python3`.

Devtool extracts only the script into a private temporary directory, preferring `$TMPDIR` when it points to a writable and searchable directory. It runs without `shell=True`, captures combined output, enforces the timeout, and removes the directory afterward. Metadata scripts are never copied into the repository.

The process receives:

```text
DEVTOOL_REPO_ROOT
DEVTOOL_WORKSPACE_ROOT
DEVTOOL_TARGET
DEVTOOL_SOLUTION
DEVTOOL_ARTIFACT_PATH
DEVTOOL_VALIDATION_TMPDIR
DEVTOOL_RUN_ID
TMPDIR
```

For `python3` validators, Devtool resolves the project interpreter and dependency manager, runs from the repository root, adds declared source roots to `PYTHONPATH`, and can use the optional `validation.script.python` table for groups, extras, validator-only dependencies, synchronization, offline mode, and editable project installation. Missing imports are reported as environment failures rather than opaque tracebacks.

Exit code `0` passes. Any other exit code or timeout fails validation and triggers transaction rollback when `rollback_on_failure` is enabled.

## Precedence and opt-outs

The resolution order is:

1. Explicit CLI option
2. Artifact manifest
3. Repository/project configuration
4. Automatic detection

Use `--no-validate`, `--no-commit`, or `--no-artifact-script` to opt out of embedded behavior. Preview and non-interactive diff operations never run embedded validation or commits.

## Creating bundles

```bash
devtool bundle overlays \
  --name "S3MO and S3OC diagnostics" \
  --repository-name sims3tools \
  --targets s3mo_s3mo_s3mo,s3oc \
  --validate \
  --validation-strategy per-overlay \
  --validation-script ./validate-bundle.sh \
  --validation-runner bash \
  --commit-message "Add S3MO and S3OC diagnostics" \
  --out "$HOME/Downloads/s3mo_s3oc_diagnostics_bundle.zip" \
  s3mo_overlay.zip s3oc_overlay.zip
```
