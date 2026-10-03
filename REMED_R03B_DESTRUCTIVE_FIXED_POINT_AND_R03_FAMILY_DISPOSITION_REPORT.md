# REMED-R03B destructive fixed-point and R03-family disposition

Status: R03B is a code-level closure candidate in WIP after the final combined validation/report pass; it is not applied or qualified in this sandbox. This report records the destructive fixed-point search, the full 25-finding R03-family disposition, final production-ingress changes, duplicate-authority removals, and validation evidence.

## Destructive fixed-point search

Search patterns: `shutil.rmtree(`, `.unlink(`, `os.remove(`, `os.rmdir(`, and shell `rm -rf` under `devtool/**/*.py`.

Current raw direct-hit count: **179** across **51** files.

This pass closed the previously unclassified high-risk remote/provider leftovers:

| Area | Previous bypass | Current disposition |
|---|---|---|
| SSH command/tmux operation staging | Command-mode/worker SSH paths still emitted raw remote shell `rm -rf` for operation stage cleanup and local artifact mirror replacement. | Raw recursive remote shell cleanup removed. Existing local/remote artifact mirrors now fail closed instead of being replaced outside LIFE/provider recovery. Remote operation staging is retained for provider lifecycle/recovery authority. |
| SSH workspace leases | Lease reclaim/release scripts used recursive shell delete. | Lease release is now owner-checked and removes only known lease marker files, then `rmdir`s the empty lease directory. Non-empty/drifted lease dirs fail closed. |
| OCI construction cleanup inside SSH command wrapping | Construction-failure cleanup removed operation roots directly. | Secret files remain cleaned as secrets; operation roots are retained for provider recovery instead of deleted without lifecycle ownership. |
| Federation sync bootstrap staging | Failed source bootstrap staging was recursively deleted. | Failed staging checkout is retained for recovery/audit; no source evidence is erased by bootstrap failure handling. |
| Federation artifact materialization drift | Drifted existing consumer materialization was directly unlinked/replaced. | Fail closed and preserve existing bytes; caller must reconcile through lifecycle authority. |
| Go package build driver | Shell driver used recursive shell cleanup for staging/publish cleanup. | No recursive shell delete; unique publish dir fails closed if present; temp cleanup removes only known planned files and uses `rmdir`. |
| Repository-pack drift GC | Pack status detected byte drift but prune could delete/tombstone the drifted pack. | Drift adds a protective cleanup reference; pack remains published and present. |
| Python `prepare-dist` | Python packaging directly removed stale `dist`. | `dist` cleanup routes through LIFE and protects tracked/source paths. |
| SSH return mirror recovery | Return recovery directly deleted local returned-evidence mirrors. | Local mirror cleanup routes through LIFE/RUN/RER revalidation. |
| PRoot staging cleanup | PRoot staging used direct recursive removal. | PRoot staging registers provider resources, releases them, and deletes only through LIFE. |

## Explicit classification for remaining raw hits

The remaining raw hits are now covered by `tests/test_remed_r03b_destructive_fixed_point.py`, which fails if a file with destructive primitives is not explicitly classified. The high-risk remote/provider files `devtool/ssh_tmux_runner.py`, `devtool/ssh_resilience.py`, and `devtool/federation_sync.py` are asserted to have no raw destructive hits.

| File | Raw hits | Classification |
|---|---:|---|
| `devtool/android/_standalone.py` | 63 | Android toolchain/provider bootstrap cache and digest-checked global SDK/NDK/Gradle cache mutation; not repository evidence cleanup. |
| `devtool/runners.py` | 13 | Runner temp cleanup plus canonical `ResolvedArtifactPlan`/apply mutation execution; not ad hoc retention/GC cleanup. |
| `devtool/transactions.py` | 11 | Private transaction journal/index/backup cleanup after completion/rollback; active/corrupt authorities fail closed before unrelated cleanup. |
| `devtool/hooks/isolation.py` | 6 | Hook-isolation exact snapshot restoration within the hook scope. |
| `devtool/federation_git_transport.py` | 5 | Git federation bundle/temp/private-ref cleanup under explicit git transport authority. |
| `devtool/artifacts.py` | 5 | Artifact temp/finalization and legacy metadata retirement; payload mutation remains plan-owned. |
| `devtool/git_dirty_state.py` | 5 | Dirty-tree backup/restore mechanics using prior snapshots. |
| `devtool/wrapper_launcher.py` | 5 | Wrapper temp/generated shim lifecycle. |
| `devtool/transaction_worktree.py` | 4 | Git transaction worktree integration/removal under transaction journal authority. |
| `devtool/repository_pack.py` | 4 | Repository-pack temp/staged cleanup; GC drift is protected. |
| all remaining one-to-three-hit files | 45 | Atomic temp-file cleanup, socket cleanup, validation marker cleanup, provider-evidence temp cleanup, or canonical LIFE/cache cleanup surfaces listed in the fixed-point test. |

## Full 25-finding R03-family disposition

| Finding | Owner | Current disposition | Evidence / remaining condition |
|---|---|---|---|
| `EXEC-A09` | `REMED-R03B` | closed in R03B WIP | Destructive cleanup reconstructs reuse-source proof from canonical EXO/RER/RUN history and fails closed on corrupt history. |
| `ENV-A05` | `REMED-R03A` | closed for current-source trace persistence | Canonical RUN/test evidence now requires/preserves current-source trace digest, source lock, root/substrate/path mapping, and federation lineage across normal ingress. |
| `ENV-A06` | `REMED-R03A` | environment-bound for G3R after production adoption proof | Code-level trace/substrate binding is present; real substrate matrix execution remains G3R evidence. |
| `ENV-A08` | `REMED-R03A` | closed in R03B WIP | Runtime Gradle task discovery now has focused proof that it uses `gradle_takeover.run_gradle_probe`, preserving typed `PROBE` intent, broker receipt identity, launcher identity, destination identity, and configuration identity instead of spawning an unbrokered Gradle task catalog probe. |
| `ANDROID-A13` | `REMED-R03A` | closed in R03B WIP | Legacy `android_instrumentation` compatibility path lowers into canonical Android execution/device authority. |
| `ANDROID-A14` | `REMED-R03A` | closed in R03B WIP; device substrate proof G3R-bound | Normal Android advanced execution ingress delegates to ANDROID-X04/EXO advanced executor with RUN lineage; real device/provider qualification remains G3R-bound. |
| `ANDROID-A16` | `REMED-R03A` | environment-bound for G3R | Simulation cannot qualify the real-device matrix; keep as G3R provider/device proof. |
| `LIFE-A04` | `REMED-R03A` | closed in R03B WIP; real-substrate execution remains G3R-bound | Android signing plan metadata now carries a non-secret LIFE-A04 transport matrix covering native, SSH command, SSH/PRoot/OCI, Termux-host chroot, and PRoot-Distro. Protected destination backends materialize secrets only at the wrapper-owned boundary and require backend staging before persistence. |
| `LIFE-A06` | `REMED-R03B` | closed in R03B WIP | Repository packs support real tar.zst creation and verification. |
| `LIFE-A07` | `REMED-R03B` | closed in R03B WIP | Historical `--commit` packs are bound to selected commit content and optional bundle evidence. |
| `LIFE-A08` | `REMED-R03B` | closed in R03B WIP | Pack registry/status/pin/unpin/prune now have durable lifecycle/reference semantics and drift protection. |
| `LIFE-A09` | `REMED-R03B` | closed in R03B WIP | Artifact prune/delete-files lowers through `CleanupOwnershipView` and preserves protected references. |
| `LIFE-A10` | `REMED-R03B` | closed in R03B WIP | Backup-shaped user data is not archived by name alone; owned backup archival uses LIFE receipts. |
| `LIFE-A11` | `REMED-R03A` | closed by R03A and preserved by R03B | Cleanup authority read failures continue to fail closed; R03B strict history/read-path tests preserve the invariant. |
| `DIST-A01` | `REMED-R03A` | environment-bound for G3R | Eight-provider parity needs actual provider runs; provider labels/simulations remain non-qualifying. |
| `DIST-A02` | `REMED-R03A` | closed for SSH production path; broader provider matrix G3R-bound | SSH provider event channel/control paths consume durable remote journal/cursor evidence instead of local loopback. |
| `DIST-A03` | `REMED-R03A` | closed in R03B WIP | Federation child re-entry now preserves non-SSH provider placement by passing `--execution-environment proot-distro`/`chroot` instead of collapsing scheduled placement back to auto/local. SSH placement remains profile-routed through `--ssh-profile`. |
| `DIST-A04` | `REMED-R03A` | closed for SSH control path; broader provider matrix G3R-bound | SSH provider control dispatch avoids local IPC fallback and uses provider session/cursor evidence. |
| `DIST-A05` | `REMED-R03A` | closed in R03B WIP | Delegation relation terminal reconciliation releases registered provider resources and records lifecycle transition. |
| `DIST-A06` | `REMED-R03A` | closed in R03B WIP | DIST-G3 final proof now consumes the DIST-X04 helper set directly: delivery health, historical projection, machine receipt, provider-observation migration, retention projection, doctor links, dogfood coverage, opaque event safety, backpressure policy, and obligation disposition. |
| `DIST-A07` | `REMED-R03A` | code-level closed in R03B WIP; real peer run remains G3R-bound | Transport negotiation rejects peers missing required protocol features (`event.envelope`, `cursor.ack`, `gap.catchup`), and SSH provider admission already derives capabilities from the remote RunStarted peer record. Real provider negotiation is still qualified in G3R. |
| `DIST-A11` | `REMED-R03B` | closed for adopted paths; real substrates G3R-bound | Whole-process SSH, command-mode SSH shell cleanup, PRoot, chroot secret staging, federation child release, OCI drift guard, and current source tracing are covered by registry/release/protection semantics. |
| `DIST-A12` | `REMED-R03A` | environment-bound for G3R | `ProviderSessionBinding` must be derived from actual qualified session/peer transport evidence. |
| `DIST-A13` | `REMED-R03A` | environment-bound/open until real reconnect run | Fresh-process reconnect still requires actual remote provider/cursor run, not local loopback. |
| `TEST-A11` | `REMED-R03A` | environment-bound/open until real provider run | Synthetic ssh-native lineage cannot qualify; real immutable remote selection must run in G3R. |

## Final combined R03B validation/report pass

The final R03B WIP validation pass was run after the latest ENV/LIFE/DIST closure batch and after refreshing the destructive fixed-point classifications.

### Combined targeted validation

Commands executed in the sandbox:

```text
python -m compileall -q devtool tests
git diff --check
python -m pytest -q <combined R03B targeted suite>
```

The all-at-once pytest invocation printed a green summary of `504 passed, 1 warning, 7 subtests passed`; the sandbox process did not terminate cleanly before the outer timeout, so the suite was re-run in bounded groups to obtain normal process exits:

| Validation group | Result |
|---|---:|
| Destructive fixed-point proof | `2 passed` |
| Retention/recovery/LIFE cleanup and pack proof | `141 passed, 1 warning` |
| Android A14/signing/instrumentation proof | `56 passed` |
| Federation/provider/DIST proof | `73 passed` |
| SSH/chroot/PRoot provider lifecycle proof | `151 passed, 7 subtests passed` |
| Gradle broker/test-runstore/Go/tasker proof | `88 passed` |

### Destructive fixed-point refresh

The destructive fixed-point classification still holds after the final batch:

- raw direct-hit count remains **179** across **51** files;
- `tests/test_remed_r03b_destructive_fixed_point.py` passes;
- no unclassified destructive primitive remains under `devtool/**/*.py` for the R03B fixed point;
- the high-risk remote/provider files `devtool/ssh_tmux_runner.py`, `devtool/ssh_resilience.py`, and `devtool/federation_sync.py` remain free of raw destructive primitives.

### Production ingress changed by R03B WIP

R03B changes production ingress instead of merely adding models:

- Android normal instrumentation ingress now delegates advanced configurations to `ANDROID-X04/EXO` authority.
- Compatibility `python -m devtool.android_instrumentation` follows the same advanced-execution takeover path.
- Runtime Gradle task discovery now uses the canonical Gradle takeover probe/broker and preserves `PROBE` intent/receipt identity.
- Canonical RUN/test evidence now requires and persists current-source trace digest, source lock, local/remote root, substrate, path mapping, and federation lineage for federated children.
- Whole-process SSH, command-mode SSH return capture/recovery, PRoot, chroot secret staging, OCI definition staging, and federation child lifecycle records now bind to provider-resource lifecycle/release semantics instead of labels alone.
- Repository-pack status/prune, artifact cleanup, Python `prepare-dist`, Go package build staging, Tasker live-state cleanup, and federation artifact materialization now fail closed or lower through LIFE/registry authority rather than deleting by name/age/drift alone.

### Duplicate authorities removed or classified

R03B removes the unowned duplicate destructive/retention authorities that were blocking closure:

- shell-level command-mode SSH return `rm -rf` cleanup;
- SSH/tmux operation staging recursive cleanup;
- SSH workspace lease recursive deletion;
- OCI definition cache drift replacement;
- federation bootstrap staging recursive deletion;
- federation artifact materialization unlink-on-drift;
- Go publish/staging `rm -rf` cleanup;
- Python `dist` direct removal;
- repository-pack drift GC deletion/tombstone;
- PRoot staging direct deletion;
- Tasker HUD/live-state direct recursive reset;
- Gradle shell takeover asset unlink outside LIFE authority.

Remaining raw destructive primitives are classified in the fixed-point proof as canonical LIFE/cache/temp/journal/transaction authority or as exact-scope restore/finalization surfaces, not open repository evidence cleanup authorities.

## Closeability

R03B is a code-level closure candidate in this WIP. The final combined R03B validation/report pass has been run, the 25-finding R03-family disposition has been refreshed, and destructive fixed-point classifications still hold.

This is still **not an applied/qualified repository seal** in this sandbox: `./devtoolw test -- ...` cannot run here because wrapper preflight rejects the environment as `native-termux: termux: desktop-linux`. The generated WIP must still be applied and validated in the real Termux/native Devtool environment.

Real-substrate proof remains intentionally carried to G3R for `ENV-A06`, `ANDROID-A16`, `DIST-A01`, `DIST-A12`, `DIST-A13`, and `TEST-A11`; those are not R03B code-level blockers because the production adoption paths are now present and the missing evidence is the unavailable real provider/device substrate.
