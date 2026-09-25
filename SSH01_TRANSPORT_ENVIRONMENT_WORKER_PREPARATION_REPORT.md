# SSH-01 — Transport, Environment & Worker Preparation

## Status

Implemented and audit-loop qualified as the first interim overlay of the three-overlay SSH campaign.

SSH-01 is **not** an SSH validation gate. It intentionally has no editable-install hook and is intended to apply with `--no-validate`. SSH-02 remains the campaign milestone gate and SSH-03 the final SSH seal gate.

## Baseline reconciliation

The fresh source archive supplied with this handoff was older than the handoff's recorded live Git state: its GIT-FRX-09 touched paths matched the exact pre-images in `devtool_git_frx09_parent_child_git_lineage_v1.zip`, while every GIT-FRX-09 added path was absent. The working tree used for SSH-01 therefore first replayed that exact audited GIT-FRX-09 payload before any SSH-01 edits.

The resulting SSH-01 artifact uses exact inventory `source_sha256`/post SHA-256 preconditions for every touched path. A synthetic Git base tree was deliberately **not** invented: the supplied archive does not contain the live Git object identity for commit `feddc32`, so source-preimage checks are the truthful fail-closed contract available for this artifact.

## Delivered architecture

### Adaptive workspace transport

- Cold or mostly-empty managed/ephemeral workspaces are scanned for source file/byte totals and nested Git boundaries.
- Archive seeding is selected only when local and remote tar/zstd are available, the source is large enough, the destination has low coverage, and no escaping symlink makes archive transport unsafe.
- The cold path streams `tar -> zstd -1 -T0 -> ssh -> zstd -d -> tar` without a permanent remote archive.
- The archive seed is never authoritative by itself: the existing rsync path always reconciles afterwards and remains the final workspace synchronization authority.
- Warm/populated and existing workspaces stay on rsync delta transport.
- Delete/exclude behavior is retained, and nested Git repositories are excluded from parent workspace transport.
- Federation-originated transport verifies the existing GIT-FRX-07 ownership context and GIT-FRX-08/Y03 + GIT-FRX-09 frozen Git lineage before bytes leave the controller.
- Sync state persists planned/effective transport mode, reconciliation state, federation identity and typed telemetry.

### Adaptive result return

- Small result sets continue to use resumable rsync.
- Large result sets can be selected remotely and returned as a zstd-compressed tar stream.
- The local return state records source bytes/files, transferred bytes, mode and compression ratio when available.
- Interrupted compression/transfer/extraction leaves the return `partial`, `pending`, or `deferred`; completion is not inferred from a transport subprocess alone.
- Existing per-run return manifests and producer provenance remain authoritative after extraction.

### Typed transfer truth

`TransferProgressChanged` is a first-class typed EXO event and a `NodeProgressChanged` subtype, so existing EXO/RunStore/HUD/TUI progress projections continue to consume canonical node progress while transfer-aware consumers receive stable transport fields:

- mode/state;
- source/transferred bytes;
- source/transferred/remaining files;
- current path;
- throughput and elapsed time;
- ETA where terminally known;
- compression ratio;
- reconciliation count;
- workspace sync ID.

Workspace archive streaming publishes periodic compressed-byte progress. Rsync reconciliation publishes terminal logical transfer/reconciliation truth. Return retrieval publishes post-return typed transfer state.

The final audit loop tightened rsync reconciliation telemetry to use machine-readable `%b`/`%l` log-format counters, so a warm delta reports bytes actually transferred instead of treating the full source tree as transferred.

### Operation-scoped SSH multiplexing

- Profile-backed SSH operations use short operation-scoped `ControlPath` values under a temp/cache-safe root suitable for Termux socket path limits.
- Probe, environment discovery, workspace metadata, rsync remote shell, tmux/SSH commands and return transport share the same operation identity.
- Multiplex-specific errors disable reuse for the rest of that operation and retry supported subprocess paths without ControlMaster options.
- Archive-seed multiplex failures are classified so the authoritative rsync fallback can proceed without repeatedly trusting a dead master.
- ControlPersist is bounded, whole-process tmux operations close their operation master eagerly, and command-mode registered masters retain best-effort process-exit cleanup.
- Multiplex evidence records enabled/disabled state, scope, control path, persist interval and fallback reason.

### Explicit remote environment policy

SSH runner configuration now exposes compatible `remote`, `overlay`, and `hermetic` policies.

- `remote` keeps remote-owned environment paths authoritative.
- `overlay` applies explicit user/config overlays while retaining leakage filtering.
- `hermetic` forwards only explicitly allowed or Devtool-owned values.
- Host-owned absolute HOME/PREFIX/PATH/PWD/TMPDIR/JAVA_HOME/Android SDK/Gradle/Python/package-cache style paths are not blindly projected onto the worker.
- Repository-bound absolute paths are translated to the remote repository root.
- Existing Termux/chroot host-context leakage protections remain in force.

### `devtool ssh env`

The new command provides text or structured JSON inventory for:

- profile/destination/execution policy;
- Termux/Linux identity, architecture, HOME/PREFIX/TMPDIR/PATH summary;
- Python, Java/JAVA_HOME, Gradle/GRADLE_USER_HOME;
- Android SDK/platforms/Build Tools/aapt2/aidl/NDK/CMake;
- Node/npm and Go;
- rsync/git/tmux/tar/zstd and build helpers;
- Devtool/Gradle/npm/Go cache roots and selected cache sizes;
- workspace and run-root accessibility;
- multiplexing evidence and probe latency;
- provenance classification (`termux-existing`, `remote-existing`, `devtool-cache`, `remote-discovery`, config/overlay where applicable).

Secret-like overlay names are redacted before structured output.

### Existing worker/toolchain reuse

Remote discovery checks Termux `$PREFIX/bin`, `$PREFIX/opt/android-sdk`, remote Android env roots, common Linux SDK locations, JDK/Gradle, Build Tools/aapt2/aidl, NDK/CMake, Node/npm, Go, tar/zstd and persistent caches. Compatible externally installed tools are represented as reusable provenance rather than being automatically redownloaded.

### `devtool ssh prepare PROFILE --target TARGET [--dry-run]`

Preparation is explicitly separated from source management:

- worker runtime requirements are classified first;
- only missing minimum worker runtime is bootstrapped when policy allows;
- target-specific toolchain readiness delegates to the existing target-aware SSH toolchain contract;
- cache/capacity/workspace readiness is inspected;
- dry-run exposes the intended preparation without mutation;
- source operations explicitly remain `clone=false`, `fetch=false`, `checkout=false`, `sync=false`.

This preserves FRX-Y11 ownership of lock-driven repository source management.

### Deep-doctor groundwork

The worker probe now carries the SSH-01 capability surface required by later SSH-03 diagnostics: latency, architecture/OS/Termux identity, free storage, tar/zstd/rsync, multiplex evidence, Java/Gradle, aapt2/aidl, Node/npm, Go, CMake/ninja, tmux, run/workspace configuration and target-aware environment discovery hooks. SSH-03 will turn this groundwork into the final remediation-oriented deep-doctor UX and storage/GC qualification.

## Native roadmap contract

Target `devtool` now declares:

- target-local job `ssh01-contract-fixture`;
- first-class test `roadmap-ssh01`;
- workflow DAG `roadmap-ssh01`;
- `roadmap-current` continuation through GIT-FRX-09 into SSH-01.

The workflow preserves GIT-FRX-09 and EXO baseline checks rather than using a custom shell validator.

## Audit loop results

Final focused/replay evidence in the construction environment:

- SSH-01 focused contract: **23/23 passed**.
- All `tests/test_ssh*.py` / `tests/test_sshw*.py`: **154/154 passed**.
- Native `roadmap-ssh01` workflow: **5/5 stages**, **50/50 tests**, **0 warnings**, **0 errors**.
- Shared-boundary regression selection (EXO event/store/progress, RunStore, HUD/TUI, GIT-FRX-07/08/09, roadmap contract, SSHW-01..06): **251 tests passed + 5 subtests**.

The native workflow was executed with `--execution-environment desktop-linux` only because the construction container is not Termux. The target remains `devtool`; no target ownership or roadmap contract was weakened.

## Explicitly deferred to later SSH overlays

SSH-01 does not claim SSH-02/SSH-03 work. Persistent historical stdout/stderr convergence, canonical sessions/runs/output/follow, generalized `ssh exec`, full recovery convergence, GC, host-key qualification UX, final deep doctor, interruption/security hardening and final performance qualification remain owned by SSH-02/SSH-03 as defined in `DEVTOOL_SSH_IMPROVEMENT_ROADMAP_V2.md`.
