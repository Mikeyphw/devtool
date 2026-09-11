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
      "tests": "pass"
    },
    "maximum_warnings": 0
  },
  "notes": [
    "Adds persistent force-close diagnostics.",
    "Does not change package parsing or export behavior."
  ]
}
```

`source_sha256` protects `modify` and `delete` actions from applying to an unexpected base file. `sha256` and `size` verify the archive payload. Source identity is computed from current file bytes; filesystem inode/size/mtime metadata is not accepted as integrity evidence. If a schema-v2 inventory is declared it must be structurally valid, and every payload file must be declared.

Schema versions are positive integers. A declared-but-malformed manifest, ambiguous sidecar/embedded manifest pair, malformed v2 inventory, unknown Git identity required by policy, or unknown clean-worktree state fails closed instead of silently falling back to legacy behavior. Boolean manifest fields are real JSON booleans; strings such as `"false"` are rejected.

`validation.expected.restore`, `validation.expected.build`, and
`validation.expected.tests` accept common outcome names: `pass`, `passed`,
`success`, `succeeded`, `skip`, `skipped`, `warn`, `warning`, `warnings`,
`fail`, `failed`, `error`, and `errors`. `validation.maximum_warnings` is a
non-negative integer and is enforced against the warnings emitted by the
resolved validation plan.

Use `--ignore-checksum` only for deliberate recovery or locally modified artifacts. It skips SHA-256 presence, format, and mismatch checks for payloads, source files, and embedded validation scripts, while retaining repository identity, path safety, inventory completeness, payload size, conflict, and validation-policy checks. Unlike `--no-manifest-check`, it does not disable the rest of artifact validation. Devtool prints a warning whenever the option is active.

Embedded validator scripts are extracted to a private temporary directory but execute with the repository root as their working directory. The temporary path remains available through `DEVTOOL_VALIDATION_TMPDIR`.

The legacy `files`, `.devtool-delete`, `repo_name`, and `validate_target` fields remain accepted.

## Optional preflight hooks

Artifacts may declare one preflight hook that runs either immediately before payload extraction or immediately after extraction, while the apply transaction is already active:

```json
{
  "preflight": {
    "phase": "before_extract",
    "write_paths": [
      "generated/",
      "gradle.properties"
    ],
    "script": {
      "path": ".devtool-artifact/preflight.sh",
      "runner": "bash",
      "sha256": "PREFLIGHT_SCRIPT_SHA256",
      "timeout_seconds": 300
    }
  }
}
```

`phase` accepts `before_extract` or `after_extract` (hyphenated `before-extract` / `after-extract` aliases are also accepted). The hook is optional at the artifact level, but when declared its nonzero exit status fails the apply and triggers transaction rollback.

`write_paths` declares every repository-relative file or directory the hook is expected to mutate. These paths are added to preview, transaction backup, and path-reservation scope before the hook executes. Git metadata under `.git/`, absolute paths, and parent traversal are rejected.

Checksum semantics are transactional rather than silently stale. Inventory source hashes are checked before the operation begins. A before-extract hook may intentionally change a checksummed source path only when that path is covered by `preflight.write_paths`; an undeclared source-hash change fails and rolls back. After extraction, the same rule applies to payload hashes: an after-extract hook may intentionally transform a payload path only when it is declared in `write_paths`, while an undeclared payload-hash or size change fails and rolls back. The artifact archive itself remains immutable: preflight receives a private temporary artifact copy through `DEVTOOL_ARTIFACT_PATH`, and Devtool also verifies that the real archive digest did not change around hook execution.

Preflight scripts share the hardened embedded-script runner with validation scripts. They must live under `.devtool-artifact/`, use `bash`, `sh`, or `python3`, declare their own SHA-256 unless checksum verification is explicitly bypassed, run from a disposable private directory, and are never extracted into the repository. In addition to the common artifact-script environment, preflight receives `DEVTOOL_PREFLIGHT_PHASE` and `DEVTOOL_PREFLIGHT_TMPDIR`.

## Optional finalization hooks

Artifacts can declare one optional `finalize` hook for the post-apply boundary immediately before the embedded validator and/or immediately before the Git commit:

```json
{
  "finalize": {
    "phases": ["before_validation", "before_commit"],
    "write_paths": ["generated/release-metadata.json"],
    "script": {
      "path": ".devtool-artifact/finalize.py",
      "runner": "python3",
      "protocol": "json-v1",
      "sha256": "FINALIZE_SCRIPT_SHA256",
      "timeout_seconds": 300
    }
  }
}
```

`phases` accepts `before_validation` and `before_commit`; hyphenated and `before_validator` / `pre_commit` aliases normalize to those canonical names. A nonzero exit status or timeout blocks the next boundary. A failed `before_validation` hook prevents the artifact validator from running. A failed `before_commit` hook leaves the validated changes uncommitted.

Finalization scripts receive a versioned JSON object on standard input and the same bytes in the owner-only file named by `DEVTOOL_HOOK_PAYLOAD_PATH`. `DEVTOOL_HOOK_PROTOCOL=json-v1` and `DEVTOOL_FINALIZE_PHASE` are also exported. The payload includes `schema_version`, `event`, `phase`, `run_id`, repository/target/solution identity, immutable artifact identity, validation state, commit intent/message/path scope, and declared `write_paths`. This makes Python hooks straightforward:

```python
import json
import sys

payload = json.load(sys.stdin)
assert payload["schema_version"] == 1
if payload["phase"] == "before_commit":
    print(f"final checks for {payload['target']}")
```

Like preflight, the final hook receives a private copy through `DEVTOOL_ARTIFACT_PATH`; metadata scripts are never extracted into the repository. Repository mutations are denied unless covered by `finalize.write_paths`. Devtool fingerprints dirty/untracked Git paths across hook execution and falls back to a repository file snapshot when Git metadata is unavailable, so changing an already-dirty path does not bypass the boundary. Declared finalization writes are included in artifact preview/transaction scope.

Use finalization for deterministic generated metadata, last-mile contract checks, or commit-time release assertions. Keep expensive compilation/tests in normal validation, and keep extraction prerequisites in `preflight` rather than overloading the final hook.

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
      "required": true,
      "sha256": "NESTED_OVERLAY_SHA256",
      "size": 12345
    },
    {
      "id": "s3oc-diagnostics",
      "artifact": "overlays/s3oc.zip",
      "target": "s3oc",
      "required": true,
      "sha256": "NESTED_OVERLAY_SHA256",
      "size": 23456,
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

`after` controls stable topological ordering while preserving declared/archive order for independent entries. `requires` additionally means the dependency must be present. Dependency cycles are rejected before changes are made. Schema-v2 bundle entries carry `sha256` and `size`, so changing nested overlay bytes after bundle creation invalidates the bundle. Missing entries are allowed only when `required` is false and no present overlay requires them. Ordered overlays may intentionally transform the same file more than once when each next `source_sha256` matches the previous output hash.

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

## Operation history

Apply history uses an idempotent schema-v3 operation record keyed by the run/artifact identity. The durable runtime record is written atomically and captures artifact content identity, child/bundle outcomes, the resolved validation plan and contract evidence, commit outcome, and explicit rollback state. On POSIX, Devtool flushes and `fsync`s the replacement file before `os.replace()` and then `fsync`s its containing directory; transaction completion/rollback summaries use the same durable write path before active recovery markers are removed. Legacy repository-local history remains readable. Cached artifact SHA-256 **and size** are carried through deferred finalization so cleanup cannot erase the recorded content identity.

## Precedence and opt-outs

The resolution order is:

1. Explicit CLI option
2. Artifact manifest
3. Repository/project configuration
4. Automatic detection

Use `--no-validate`, `--no-commit`, or `--no-artifact-script` to opt out of embedded behavior. Preview and non-interactive diff operations never run embedded validation or commits.

## Creating bundles

Bundle creation is fail-closed: every generated schema-v2 child entry is pinned by byte size and SHA-256, and the completed bundle is strictly self-validated before the creator reports success. A child overlay whose own manifest/inventory checksum is invalid therefore cannot be packaged merely because the outer child hash is correct.

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

## Deferred validation policy

Schema-v2 artifacts may make staged validation explicit:

```json
{
  "validation": {
    "mode": "required",
    "allow_deferred": true,
    "minimum_phase": "finalize",
    "rollback_on_failure": true
  }
}
```

`apply-overlay --defer-validate` applies transactionally, preserves the backup, retains the source artifact until finalization, and creates an active validation session. An embedded artifact validator becomes the `static` session phase, so it is checkpointed rather than postponed invisibly to the end. Commit and artifact cleanup occur only after the session completes. `validation.allow_deferred=false` rejects this mode.

Advance the session with one repeatable command:

```bash
devtool -r REPO --target TARGET validate continue
```

With no selector, `validate continue` runs exactly the next pending phase. A phase interrupted with Ctrl+C or Control Center cancellation is returned to `pending`, not cached as passed or failed, and does not trigger rollback. A phase left as `running` by process death is reclaimed automatically on the next continue/status/checkpoint command. The matching deferred-apply transaction is converted back to pending-validation state, so stale-apply recovery never mistakes an interrupted validation checkpoint for an abandoned repository apply.
