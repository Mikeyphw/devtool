# Devtool SSH Remote Execution

This guide describes the completed SSH-01 → SSH-03 remote-execution contract. The local repository remains authoritative for source identity; SSH workers provide execution capacity, durable output, recovery, and returned artifacts. Federation source ownership and frozen-source rules remain owned by GIT-FRX-07/GIT-FRX-08/FRX-Y03/GIT-FRX-09 rather than by the SSH transport.

## Core model

A named SSH profile resolves an OpenSSH destination, worker policy, workspace policy, execution provider, environment policy, resilience policy, and artifact-return policy. Managed and ephemeral workers synchronize source from the local authoritative repository; `ssh prepare` never clones, fetches, checks out, or otherwise acts as source synchronization. FRX-Y11 later owns lock-driven clone/fetch/update semantics. FRX-Y09 consumes the worker capability/provider evidence produced here.

SSH-02 made the persisted run directory and EXO/RunStore evidence authoritative for history. tmux hosts detached execution but is not the history database. stdout and stderr remain independently persisted after tmux exits or a client disconnects.

## Inspect and prepare

Use these before remote execution:

```bash
devtool -r ~/Code/project ssh doctor PROFILE --target TARGET
devtool -r ~/Code/project ssh env PROFILE --target TARGET
devtool -r ~/Code/project ssh prepare PROFILE --target TARGET --dry-run
```

`ssh doctor` reports worker platform/architecture, execution-provider capability, toolchain inventory, remote capacity, OpenSSH host identity evidence, lifecycle/orphan state, and concrete remediation commands. `ssh prepare` may install missing worker/toolchain pieces according to policy, but it does not change repository source.

Host identity is intentionally derived from OpenSSH `known_hosts`. Devtool does not maintain a second key database. The doctor surface reports key algorithms and SHA-256 fingerprints without returning public-key material. A changed key remains an OpenSSH policy error governed by `StrictHostKeyChecking` and `UserKnownHostsFile`.

## Execute and inspect history

Generic managed execution reuses the same worker/workspace/environment/multiplex/tmux/recovery machinery as normal Devtool work:

```bash
devtool -r ~/Code/project ssh exec PROFILE --target TARGET -- command arg1 arg2
devtool -r ~/Code/project ssh exec PROFILE --target TARGET --detached -- command
devtool -r ~/Code/project ssh exec PROFILE --target TARGET --follow -- command
devtool -r ~/Code/project ssh exec PROFILE --target TARGET --shell 'explicit shell text'
```

Argument mode preserves argv boundaries. Shell interpretation happens only with `--shell`.

History and output are persistent:

```bash
devtool -r ~/Code/project ssh sessions PROFILE
devtool -r ~/Code/project ssh runs PROFILE
devtool -r ~/Code/project ssh output PROFILE --latest
devtool -r ~/Code/project ssh output PROFILE --run-id RUN_ID --follow
devtool -r ~/Code/project ssh output PROFILE --run-id RUN_ID --stream stderr
```

A dead tmux session does not erase history. Output state is classified as complete, partial, still-writing, unavailable, or interrupted from the persisted phase state and stream files.

## Recovery

```bash
devtool -r ~/Code/project ssh resume PROFILE --run-id RUN_ID
devtool -r ~/Code/project ssh cancel PROFILE --run-id RUN_ID
devtool -r ~/Code/project ssh recover PROFILE --run-id RUN_ID
devtool -r ~/Code/project ssh retrieve PROFILE --run-id RUN_ID
```

`ssh recover` marks stale remote phases interrupted, cleans secret staging, releases only leases owned by that run, and in SSH-03 also repairs old inactive orphan leases whose owner run no longer exists. Active, young, corrupt, or ambiguously owned leases remain protected. Recovery never silently reruns already-successful phases.

## Capacity and ENOSPC prevention

Before a managed/ephemeral source sync, SSH-03 estimates repository payload size and combines it with workspace-mode headroom plus the explicit `resilience.min_free_mb` floor. If the worker reports known free space below the estimate, execution is blocked before synchronization with a diagnostic describing free space, estimated requirement, repository size, and configured floor. Unknown free-space probes are reported as unverified rather than fabricated.

The estimate is a safety guard, not a performance benchmark.

## Safe history GC

GC is dry-run by default and only targets Devtool-owned terminal run directories:

```bash
devtool -r ~/Code/project ssh gc PROFILE --dry-run
devtool -r ~/Code/project ssh gc PROFILE --dry-run --max-age-days 30 --keep-last 20 --max-mib 512
devtool -r ~/Code/project ssh gc PROFILE --apply --max-age-days 30 --keep-last 20 --max-mib 512
```

Safety rules:

- active runs are never selected;
- stale/interrupted/recovery-sensitive runs are never selected;
- the newest `--keep-last` runs are protected;
- unsafe run IDs are protected;
- symlinked or path-escaping run directories are rejected;
- deletion is root-bounded to the configured Devtool SSH run-state root;
- size policy uses least-recently-used terminal order (persisted finish/start activity), oldest first;
- age/count/size decisions are returned as structured evidence.

Run `ssh recover` before GC when interrupted or stale records need reconciliation.

## Performance qualification

`ssh qualify` records source-sync, tmux, return, and optional project-validation timings. Warm/incremental behavior is evidence, not a hard wall-clock SLA. The final SSH gate intentionally avoids fragile timing thresholds because worker hardware, network conditions, Android/Termux scheduling, and repository size vary.

## Security boundaries

Remote command metadata must contain the remote repository/workspace path, never a local Termux/chroot path. Sensitive environment values are staged through mode-600 files rather than command argv. Host key verification stays with OpenSSH. GC and lease repair perform canonical-path containment checks before deletion. Nested repositories remain separate federation ownership boundaries.

## AI-agent operating sequence

For an agent operating any repository through Devtool SSH:

1. Resolve the intended target and named SSH profile.
2. Run `ssh doctor PROFILE --target TARGET`; inspect host identity, platform/architecture, provider, capacity, and remediation.
3. Run `ssh prepare PROFILE --target TARGET --dry-run`; apply preparation only when the plan is appropriate.
4. Use normal Devtool workflows or `ssh exec`; do not invent custom source synchronization.
5. Inspect `ssh runs` and `ssh output` rather than relying on live tmux state.
6. On interruption, prefer `ssh resume`, `ssh recover`, and `ssh retrieve` before rerunning work.
7. Use `ssh gc --dry-run` first; apply cleanup only after reviewing protected/candidate classifications.
8. Preserve GIT-FRX frozen-source and lineage authority. SSH transport evidence is consumed by later federation milestones; it does not replace them.
