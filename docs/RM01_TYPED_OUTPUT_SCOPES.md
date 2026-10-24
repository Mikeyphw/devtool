# RM-01 — Typed output scopes and owned workflow temporary outputs

`DT-060` implementation on top of RM-00. Target-local jobs and repository jobs
may declare `output_scopes` using TOML inline tables or an array of tables.
The scheduler uses the same existing static-wave safety authority; RM-02 will
replace wave barriers, not duplicate reservations.

```toml
[targets.api.jobs.test]
runner = "command"
command = ["python3", "scripts/check.py"]
inputs = ["scripts/**"]
output_scopes = [
  { kind = "run-temp", path = "reports/report.json", lifetime = "run", cleanup = "on-terminal", hash_retention = "always" },
  { kind = "shared-cache", path = "wheels", reservation = "pip-download" },
  { kind = "repository", path = "build/outputs" },
]
```

* `repository`: relative to the job's cwd; cannot escape its repository,
  traverse through symlinks, or write inside `.git`.
* `run-temp`: relative to `$DEVTOOL_WORKFLOW_TMPDIR`, an exclusively created
  private directory under XDG state for the current run/node. It is removed
  after the scheduler has captured the requested file digest and node results.
  **Do not** use it for outputs that must survive beyond the workflow; declare
  such results under `repository` instead. No directory-archive hashing is
  promised; file hashes are attached to result metadata when `hash_retention`
  is `always` or `on-success`. The future RM-05 evidence graph will extend this.
* `shared-cache`: a logical reservation under the repository's Devtool cache
  namespace. This does not automatically create a cache or change runner
  environment; jobs continue to manage actual cache use.
* `external`: literal absolute path **and required** `reservation` key. This
  records a conservative external-write conflict but does **not** authorize the
  command to write to that path or sandbox external processes.

Required shared resource keys are also honored for caches/external paths.

All declared `output_scopes` must be valid, unique tables. `kind`, `path`,
`lifetime`, `cleanup`, `hash_retention`, `reservation` are the only supported
fields. Non-temporary outputs require `lifetime = "persistent"` and
`cleanup = "none"` and `hash_retention = "never"`. Temporary outputs
currently require `lifetime = "run"` and `cleanup = "on-terminal"`; an
unimplemented `node` lifetime is deliberately rejected rather than silently
being treated like `run`. Unknown writes retain their old conservative serialization.
A job may **not** claim `read_only = true` while declaring typed outputs.

**Safety and limitation:** Existing processes are not forcibly confined to
these paths. Scheduling reservations are *declared* scopes, not a filesystem
sandbox. Physical untracked writes or undeclared aliases require `global_reservations`
and/or sequential execution. Temporary-root cleanup is inode checked and
refuses to remove a replacement directory; malicious concurrent filesystem
mutation is outside the scope of this API. A normal successful or failing
workflow cleans its owned temp roots; if the process is killed abruptly, roots
can remain for later explicit recovery (a future lifecycle capability).

Run-level event schemas remain unchanged. The new `output_scopes` list and
`workflow_temporary_output_hashes` metadata are additive to existing plans and
node results; the canonical provenance graph is completed in RM-03 / RM-05.

## Test and target validation

Overlay RM-01 is source bound to the exact RM-00 post-install *Git tree*, not
its device-specific commit timestamp or author. Preflight also verifies the
RM-00 contract files and target configuration. Post-apply validation is run
in-process for the native `devtool` target (no nested CLI), followed by
`test_rm01_output_scopes.py`, the pre-existing MP09 scheduler regression suite,
and `test_rm00_shared_contracts.py`. Failed validation rolls back the overlay.


## FC-01 safety closure (2026-10-24)

The historical limitation above is superseded **only for declared owned
run-temp roots** by `devtool/output_scopes.py`: individual path components
are opened through anchored directory descriptors using `O_NOFOLLOW`,
`fstat`/inode identity checks, and descriptor-relative `unlink`/`rmdir`.
No pathname `shutil.rmtree` is used. A swapped root or child refuses deletion,
and hashing refuses directory escapes, special files, symlinks, hardlinks
(`st_nlink != 1`), and mutable files. An unprovable identity yields an
unavailable/refused receipt, never a digest of outside bytes.

Each provisioned root gets a private `.owner-<node>.json` lease record alongside
the owned root, exclusive-created, checksum-free but inode-bound, and fsynced
before a job is spawned. The owning process holds an exclusive kernel `flock`.
`recover_orphaned_temps(repo_root, dry_run=True)` enumerates known records
and returns `orphan_candidate`, `preserved` or `refused`. With `dry_run=False`
it reclaims **only** an unlocked, correctly formed record whose exact root
inode still exists. The provisioner calls that conservative recovery before
starting a new run. **Automatic reclamation requires proof that the journal
belongs to a previous boot**, because after SIGKILL a subprocess may still be
writing outputs even when the supervisor's lock has been released. Same-boot
crash roots require explicit approval through
`python3 -m devtool.output_scopes --repo REPO --recover --approve-same-boot`
after checking descendants. Read-only inspection uses `--repo REPO` alone.
Corrupt/missing journals, swapped inodes and live leases
are *never* authority to delete. A process killed during initial creation
before journal durability may leave an **unregistered** root: this is surfaced
as an `orphan_candidate`, intentionally preserved for operator review.

The scheduler includes a `workflow_temporary_cleanup` result with `cleaned` or
`refused` and the owned inode pair. `workflow_temporary_output_hashes` retain
individual per-file proofs or explicit unavailability. No command process
is sandboxed: an adversary with write access *inside an actively owned* root
can interfere with its content; descriptor anchoring limits the authority of
outside pathname swaps but is not a kernel filesystem sandbox. On a platform
without POSIX descriptor/no-follow locking semantics, provisioning must refuse.

Safety regression tests: `tests/test_fc01_safety_closure.py` plus the original
RM01 scheduler suite. Linux and Termux acceptance are separate evidence gates.

### FC-01 Android/Termux traversal portability correction (2026-10-24)

Ancestor directories, including `/`, may be searchable but not readable by
Termux under Android SELinux. FC-01 therefore pins ancestors with Linux
`O_PATH|O_DIRECTORY|O_NOFOLLOW` and opens **only** its final owned directory
for reading and listing. `O_NOFOLLOW` stays in force throughout; neither
hashing nor cleanup falls back to a pathname-based recursive operation.
On platforms without `O_PATH`, pre-existing no-follow read-descriptor traversal
remains available only when permitted. If descriptor traversal cannot be
established, the operation fails closed. This addresses device run
`aea717e2f5c54eaeb2ac0f5446593c56`, which failed reading `/` before
any RM01 test ran; real-device acceptance remains a separate gate.
