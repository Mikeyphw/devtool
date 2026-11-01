# R38-R2 — Owned scratch/cache routing and Node 24 CI actions

## Contract

`DEVTOOL_R38_SCRATCH_ISOLATION=enforce` enables **opt-in** per-workflow-node environment-based scratch and cache separation. Default is `off`; dry runs do not provision scratch. It uses the pre-existing RM-01/FC-01 output-scopes owner journal, no-follow dirfd creation, leased inode identity, and terminal cleanup; it does not establish an OS sandbox.

On native workflow execution, every node receives a separate private leased directory outside the repository. `DEVTOOL_WORKFLOW_TMPDIR` and `DEVTOOL_R38_SCRATCH_ROOT` identify the leased node root. `TMPDIR`, `TMP`, and `TEMP` point to a private `tmp/`; `XDG_CACHE_HOME`, `PIP_CACHE_DIR`, `npm_config_cache`, `GOCACHE`, `GRADLE_USER_HOME`, `UV_CACHE_DIR`, `PYTHONPYCACHEPREFIX` point to separate private cache directories under the same root. Each node uses an independent path, including when safe parallel scheduling is enabled. Tools must honor those environment variables to benefit.

The scheduler refuses attempts to shadow reserved routing keys in any command spec **before creating owned directories**. No process-global `os.environ` changes occur. Historical execution reuse is disabled in enforcement mode because reuse bypasses per-command routing checks. A node with an existing `run-temp` output scope **reuses the same leased root** rather than allocating a competing owner. Terminal cleanup retains the pre-existing refusal/identity evidence when a path cannot be safely removed. On errors provisioning a node, previously minted roots are rolled back using the owner-aware cleanup path.

## Explicit limits

- No kernel namespace, chroot, container, network, process, or filesystem confinement; commands can still access absolute locations and ignore the routed environment.
- No assertion that a tool with explicit user/config cache paths writes only to the leased area. `GRADLE_USER_HOME` changes Gradle's user-home and may omit host init scripts, settings, credentials, or warmed caches; turn on this mode only when those effects are acceptable. No Gradle invocation is part of local tests.
- Persistent/shared-cache output scopes are **not** silently redirected. Those require separate source-stamped cache integrity qualification and reservation proofs in later R38 work.
- A process may attempt to replace or move a scratch path after provisioning. Descriptor-owned cleanup refuses unsafe replacements; cleanup refusal appears in `workflow_temporary_cleanup` and is not a proof of successful removal.
- Native workflow nodes only; empty/non-workflow command lists cannot gain an isolation claim. Cancel and failed paths retain the normal owned-output cleanup lifecycle. Formal real-host isolation is not qualified by these source tests.

## Node.js 20 warning

The manual 24-shard regression workflow now uses Node 24-compatible first-party Action majors: `actions/checkout@v6`, `actions/setup-python@v6`, `actions/upload-artifact@v5`, `actions/download-artifact@v5`. These change the **JavaScript action runtime**, not the Python 3.13 test version. Do not use the Node 24 force flag to hide old Action metadata warnings. Other historical Android/FC-08 workflows still carry older tags and separately pinned SHAs and are **not** upgraded by this focused regression-workflow repair; validate them individually against their source/evidence commitments.

## Gates

1. Source-pinned Devtool transactional apply with targeted tests and automatic commit.
2. Exact resulting SHA: manual GitHub Actions 24-shard regression, summary must be 24/24 and all modules present.
3. Independent real-host mode-on/mode-off evidence for scratch/cache routing, concurrency, cancellations, permissions and cleanup, before describing R38-R2 as real-host qualified.
4. Check the full-regression run's warnings/annotations to confirm the Node.js 20 deprecation warning is absent; tests only prove the workflow source references the supported Action majors.
