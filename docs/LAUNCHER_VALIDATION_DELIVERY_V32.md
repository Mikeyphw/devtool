# Devtool Launcher v3.2 — native validation-aware immutable overlays

## Trust and execution split

The immutable **launcher** is source and payload attestation + orchestration only.
The existing `devtool.delivery_adoption` emits the native v2 archive, the
existing `validate-overlay` checks the archive, the native `apply overlay`
executes hooks/tests and commits transactionally, and the existing delivery
session owns push/PR/CI receipts. This upgrade introduces **no** replacement
Git, validation, session, Gradle, or CI engine.

Existing immutable v3.1 packages retain their exact original payload/runtime
provenance. The v3.2 builder creates new versioned 3.x-compatible packages; it
must never overwrite or silently repin an older ZIPAPP.

## Source-bound build plan

Prepare a clean named feature branch, an independent trusted Devtool runtime,
and a data-only plan in a private directory outside the repository:

```json
{
  "schema_version": 1,
  "changes": [
    {"path": "tools/test_community_actions_contract.py", "action": "modify"}
  ],
  "delivery_validation_mode": "full",
  "validation": {
    "mode": "required",
    "required": true,
    "restore": false,
    "build": false,
    "test": false,
    "rollback_on_failure": true,
    "tests": [
      {
        "id": "community-contract",
        "runner": "python",
        "command": ["python3", "-B", "-m", "unittest", "discover",
                    "-s", "tools", "-p", "test_community_actions_contract.py", "-v"]
      }
    ],
    "script": {
      "path": ".devtool-artifact/validate.py",
      "runner": "python3",
      "protocol": "json-v1",
      "sha256": "<SHA256_OF_VALIDATOR_BYTES>",
      "timeout_seconds": 300
    }
  }
}
```

Supply the metadata script in the builder's `--payload` directory at the same
`.devtool-artifact/validate.py` path, alongside the changed-file payload.
The builder checks the script's path, symlink ancestry, size and SHA-256, then
embeds its immutable bytes in the ZIPAPP separately from the repository
inventory. The S7 native scaffold carries the same bytes into the native v2
archive as disposable **metadata-only** hooks. Devtool's own hook runner owns
capability checks, execution evidence, structured results and rollback.

The native overlay must declare **required executable validation** before a
v3.2 full-mode delivery. A static plan declaring tests, tasks, or lifecycle restore/build/test phases is
refused rather than silently skipping checks. An explicit static-only validator
is allowed for projects with no locally supported runner when the plan includes
`delivery_static_justification` (12–320 characters), `validation.required=true`,
a checksum-verified `validation.script`, and `restore/build/test=false`.
The report must say full qualification was not performed. Existing v3.1 immutable
packages continue to work unchanged. New v3.2 builder invocations using a
legacy two-key S7 plan must explicitly opt into `--allow-legacy-static`;
otherwise building a no-test package is refused.

`delivery_validation_mode=deferred` currently refuses: deferred validation has
its own durable transaction/continuation boundary and must not be mistaken for
a committed, pushable native delivery session. Do not replace this refusal with
an ad-hoc commit, reapply, or fake completion.

## Terminal monitoring

With a real POSIX stdin/stdout/stderr TTY and no `NO_COLOR`, `CLICOLOR=0`,
`TERM=dumb` or `CI=true`, the **native CI observation** child inherits those
file descriptors directly and uses its existing `NativeRichMonitor` inline
(`screen=False`). The launcher never spoofs a TTY, proxies keyboard events, or
reimplements GitHub polling. For piped/non-TTY runs, the existing plain output
relay is retained. A direct-TTY child bypasses the launcher's byte transcript,
so the launcher never calls that failure transcript complete; native durable
CI receipts and native logs remain authoritative.

To observe CI during delivery, run an immutable package with `apply
--approve-remote --watch-ci`. If the package committed and the watch was
interrupted/failed, **never apply again**. Use the original package's
`status`, `report`, `ci`, and `deliver` with the durable session ID.

## Acceptance matrix

- **Static**: exact origin, branch, HEAD/tree/blobs, content hashes, native
  archive shape/source check, embedded validator when explicitly present.
- **Full**: additionally executed runner-native `validation.tests`/`tasks` and
  embedded validator, native result/transaction evidence, commit only on pass.
- **Hosted**: required workflow identities at the actual committed PR HEAD;
  zero local/device/release claims inferred from hosted status.
- **Device/release**: qualified separately, never implied by overlay success.

Test with `tests/test_launcher_validation_delivery.py`, the original launcher
contract/adoption tests, artifact-validator payload negatives, native
`validate-overlay`, native apply and durable CI receipts. Do not delete or
bypass existing regression/security gates to make validation green.
