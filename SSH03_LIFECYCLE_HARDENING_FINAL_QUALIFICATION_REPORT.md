# SSH-03 Lifecycle Hardening & Final Qualification

## Scope

SSH-03 closes the three-overlay SSH roadmap after SSH-01 transport/environment/worker preparation and SSH-02 persistent execution/history/recovery convergence. It is the final SSH validation gate.

## Delivered behavior

- `devtool ssh gc PROFILE` with dry-run-by-default age, count/keep-last, LRU, and size policies.
- GC protects active, stale, interrupted, unsafe-ID, and path-escaping state; deletion is bounded to the configured Devtool run root.
- stale phase recovery converges with old inactive orphan workspace-lease repair without rerunning successful phases.
- OpenSSH `known_hosts` remains the host-key authority; doctor exposes algorithm and SHA-256 fingerprint evidence without creating a parallel key database.
- worker identity evidence combines destination, platform, OS, architecture, and provider/toolchain inventory.
- `ssh doctor --target TARGET` adds target-aware remediation and preparation commands.
- repository-aware free-space estimation blocks obvious ENOSPC launches before managed source synchronization.
- qualification records performance evidence without fragile wall-clock thresholds.
- local Termux/chroot paths remain isolated from remote run metadata; path-sensitive cleanup uses canonical-root containment.
- final human/AI operating guidance is in `docs/SSH_REMOTE_EXECUTION.md`.

## Promise ledger

| Promise | Final authority/evidence | Status |
|---|---|---|
| Adaptive seed/delta/return transport | SSH-01 transport telemetry | Delivered |
| Explicit remote/overlay/hermetic env | SSH-01 environment evidence | Delivered |
| Worker/target preparation without source sync | SSH-01 prepare contract | Delivered |
| Persistent run/output/history | SSH-02 run directory + EXO/RunStore | Delivered |
| Generic managed remote exec | SSH-02 `ssh exec` | Delivered |
| Resume/cancel/recover/retrieve | SSH-02 + SSH-03 orphan repair | Delivered |
| Artifact return lifecycle | SSH-02 return manifest/state | Delivered |
| Safe history GC | SSH-03 lifecycle policy | Delivered |
| OpenSSH host identity/key-change diagnostics | SSH-03 known_hosts evidence | Delivered |
| Capacity / ENOSPC prevention | SSH-03 pre-sync capacity estimate | Delivered |
| Target-aware doctor/remediation | SSH-03 doctor evidence | Delivered |
| Interruption/security/path hardening | SSH-02 atomic persisted streams + SSH-03 bounded cleanup | Delivered |
| Performance qualification without fragile thresholds | `ssh qualify` metrics + SSH-03 fixture | Delivered |
| Human + AI guide | `docs/SSH_REMOTE_EXECUTION.md` | Delivered |
| FRX-Y09 capability/provider handoff | doctor/environment/provider evidence | Ready for consumer |

## Final-gate intent

The final gate validates SSH-01, SSH-02, SSH-03, the complete SSH regression family, GIT-FRX-09 lineage, workflow/EXO baseline, and source syntax. The native `roadmap-ssh03` workflow explicitly exercises transaction-candidate editable installation. Artifact validation executes from the isolated transaction worktree, then its single before-commit hook refreshes the stable primary checkout so promotion exposes the validated source without leaving Devtool bound to a disposable worktree.

No SSH-03 performance assertion depends on a fixed wall-clock threshold. No host-key material or alternate key database is persisted by Devtool.

## Audit-loop evidence

Final working-tree qualification after the explicit LRU evidence pass:

- SSH + SSHW + native roadmap contract selection: **196 passed**.
- `roadmap-ssh03`: **9/9 stages**, **280 passed**, **0 failed**, **0 skipped**, **0 warnings**, **0 errors**.
- The gate includes transaction-candidate editable install, SSH-03 final fixture, GIT-FRX-09 lineage, SSH-01, SSH-02, SSH-03, the full SSH regression selection, and EXO/workflow baseline.
- The final artifact is required to replay these checks from the exact post-SSH-02 preimage before release.
