# ENV-X01 — Host truth, scoped privilege and capability-first substrate foundation

Date: 2026-09-28

## Campaign position

- Gate-window position: ENV gate window 1 of 3 (`ENV-X01` → `ENV-X02` → `ENV-G1`).
- Major-scope position: ENV overlay 1 of 7.
- Full-plan position: 11 of 63.
- Merge-window decision: **do not merge**. `ENV-X02` owns Python/Go environment composition and mise/Poetry/direnv; `ENV-G1` is a separate authoritative gate. ENV-X01 remains the host/substrate/privilege foundation they consume.
- Base: post-CORE-X04 content corresponding to the successful `4ba98f5` apply. The build workspace reconstructs those bytes at local commit `cf90417`.

## Delivered merged-program promises

ENV-X01 delivers the planned foundation for:

- `ENV-P001` canonical `HostContext` separating real host OS from execution substrate.
- `ENV-P002` typed native/chroot/PRoot/SSH/OCI/provider candidate identities.
- `ENV-P003` stable capability IDs and typed `PrivilegeRequest`/provider/qualification/receipt contracts.
- `ENV-P004` repository/artifact capability requests cannot grant privilege; `[privilege]` is global-only trusted policy.
- `ENV-P005` privilege qualification distinguishes binary presence from ready/action-required/denied/unavailable/error.
- `ENV-P006` passive doctor/HUD/notification paths do not trigger root authorization.
- `ENV-P007` PRoot/emulated uid 0 cannot satisfy real host-root capabilities.
- `ENV-P008` chroot entry privilege is separate from guest command identity.
- `ENV-P009` named guest identity/drop remains the chroot execution contract after privileged substrate entry.
- `ENV-P010` `CapabilityRequirements.from_specs()` derives tool/capability/network/feature intent from planned command specs.
- `ENV-P011` candidate selection is qualification-first, least-privilege biased, and refuses action-required privilege as automatically ready.
- `ENV-P012` `EnvironmentQualificationReceipt` records accepted/rejected candidates and reasons.
- `ENV-P013` privilege/environment qualification caches are short-lived, environment-scoped, TTL-scoped and explicitly invalidatable.
- `ENV-P014` host-global resource mutations such as `drop_caches` require explicit global privilege policy.
- `ENV-P015` SSH provisioning privilege is declared separately from routine SSH execution privilege.
- Early foundation for `ENV-P029`: repository/job/target environment overlays cannot forge protected Devtool execution, transaction, IPC, federation, chroot, root-bridge or SSH identity variables.

## Implementation

### Host truth

Added `devtool.host_context` with a canonical `HostContext` and stable identity digest. Native Termux is represented as Android host + `native-termux` substrate; a real Linux chroot is not mislabeled as native Termux; SSH/provider identity is kept as a separate axis; macOS, Windows and unknown hosts no longer collapse to desktop Linux.

`host` execution-environment syntax now resolves to the current host substrate rather than hard-coding desktop Linux.

### Scoped privilege broker

Added `devtool.privilege` with:

- purpose-scoped capability IDs;
- `PrivilegeRequest`, `PrivilegePolicy`, `PrivilegeProviderQualification`, `PrivilegePlan`, and `PrivilegeReceipt`;
- direct-root, root-bridge, Android `su`, `sudo`, and `doas` qualification;
- global-only policy loading;
- passive-by-default qualification;
- non-interactive provider semantics and short-lived qualification caching.

Chroot entry no longer defaults to `sudo`. Explicit legacy `DEVTOOL_CHROOT_SUDO` remains a compatibility override, while the default path is broker-owned.

### Capability-first substrate qualification

Added `devtool.environment_qualification` with:

- `CapabilityRequirements`;
- typed execution candidates;
- native/chroot/PRoot/SSH/OCI qualification contracts;
- explainable rejection reasons;
- least-privilege selection;
- receipts, identity digests, TTL-aware caching and invalidation;
- fail-closed guest/remote tool qualification when no destination receipt exists.

An `action-required` privilege provider is deliberately **not** sufficient for automatic environment selection.

### Protected environment namespace

Repository, job, target and variant environment overlays are checked before execution. Protected Devtool run/IPC/transaction/federation/execution/chroot/root-bridge/SSH state cannot be forged through lower-trust config.

### Passive diagnostics and presentation

Doctor reports host context and passive privilege/provider state without executing `su`/`sudo` probes. Android HUD package discovery and apply notifications no longer have implicit root fallbacks in passive presentation paths.

### Resource privilege policy

The full runtime cache-reclaim path now uses the privilege broker and fails closed unless `linux.vm.drop_caches` is globally authorized and a provider is qualified. The staged standalone Android builder never self-elevates for cache reclaim because it cannot own or authenticate global privilege policy.

### SSH privilege separation

SSH worker bootstrap evidence now states the provisioning privilege requirement separately and records that routine SSH execution itself does not require privilege.

## Additional Devtool bugs found and absorbed into ENV-X01

1. Privilege provider executable discovery initially used the Devtool process PATH instead of the supplied qualification environment PATH. Fixed; provider qualification is environment-scoped.
2. Environment selection could treat an `action-required` provider as sufficiently qualified and auto-select it. Fixed; automatic selection requires actual readiness.
3. Environment qualification cache identity omitted requested TTL, allowing differently scoped cache requests to alias. Fixed; TTL is part of the cache identity and cache policy caps receipt lifetime.
4. Android HUD package discovery could invoke `su` from a read-only rendering path. Fixed; privileged discovery now requires explicit opt-in.
5. Apply notifications could fall back to a root `cmd notification` operation from a presentation path. Removed; notification delivery is privilege-passive.
6. Standalone Android memory-pressure cache reclaim could discover `su`/`sudo` and elevate without canonical policy. Fixed; only the full broker-owned runtime may perform the host-global mutation.
7. Chroot entry had an implicit `sudo` default even on Termux/root-bridge capable hosts. Fixed; provider selection is broker-owned unless an explicit legacy override is supplied.
8. Host detection previously collapsed non-Termux hosts to desktop Linux. Fixed with explicit macOS/Windows/unknown identities.
9. Build resource policy re-detected the ambient Python platform after HostContext resolution, so a synthetic desktop environment on a real Termux/Android process still received the Android/default four-worker cap. Fixed by resolving build policy from an explicit environment snapshot; synthetic desktop now remains `desktop-linux` with the desktop cap while real Termux markers still resolve to `native-termux`.

## Separate bug discovered and integrated into the existing campaign

`tests/test_rem03_cli_config_discovery.py::test_feature_flags_and_doctor_use_explicit_config_path` fails on the untouched post-CORE-X04 baseline: an explicit config located inside the requested repository (`<repo>/configs/custom.toml`) is interpreted as owning the nested `configs/` directory, causing a repository-config ownership mismatch.

This is **not ENV-X01-owned**. It is added to the existing `CORE-G3` downstream-consumer/final-seal audit as a repository-identity/config-discovery compatibility obligation. ENV-X01 does not weaken repository ownership rules to hide it.

The already-known non-interactive terminal clear issue (`TERM environment variable not set`) remains assigned to `PRES-X04/PRES-G3`.

## Apply-failure remediation and Devtool-native validation

The first live Termux apply exposed an `ENV-P001` portability defect: sparse explicit environment mappings inherited the parent Android `platform.system()` identity, so `{"HOME": "/home/mike"}` was misclassified as `host-android` instead of `desktop-linux`. The corrected HostContext treats an explicitly supplied environment mapping as authoritative model input, consumes portable OS hints when present, and otherwise preserves the historical POSIX/Linux default without leaking the caller host. A regression patches the ambient platform to Android and proves the sparse Linux mapping remains `desktop-linux`.

The second live Termux apply exposed the remaining half of the same authority leak: `resolve_build_policy()` called host detection without an explicit environment snapshot, so the caller's Android platform still overrode a test/delegated desktop environment and clamped the desktop policy to four workers. ENV-X01 now makes the environment snapshot authoritative at the resource-policy boundary as well. Regressions prove `desktop-linux -> 8 workers` for the synthetic desktop case and `native-termux -> 2 workers` for the constrained Termux case under the same ambient Android platform.

ENV-X01 qualification is also migrated from an ad-hoc `validate.sh` pytest list to Devtool's own artifact/test surfaces. `.devtool.toml` declares durable test identity `roadmap-env-x01` and reusable workflow `roadmap-env-x01`; the overlay manifest references that first-class test through `validation.tests`. The parent apply run therefore owns selection, runner metadata, test normalization, progress/evidence, and rollback decisions without unsafe nested Devtool CLI re-entry. Manual replay is `./devtoolw test -- roadmap-env-x01`.

The replay also exposed a lifecycle defect in the before-commit editable-source refresh: importing `devtool` from inside the transaction candidate can shadow the installed editable distribution and falsely conclude the primary checkout is not bound. The artifact hook now inspects installed distribution `direct_url.json` metadata instead of importing from the candidate. The general transaction-root-aware self-refresh contract is exported to `LIFE-X01` / `LIFE-G1`.

## Validation

Bounded non-Gradle implementation validation after final ENV-X01 changes:

```text
437 passed, 7 subtests passed
```

Covered host identity, privilege policy/provider qualification, environment qualification, chroot execution, protected config/job/target env overlays, Android HUD/instrumentation/runner behavior, config/global config, doctor, project runners, Termux runner behavior, SSH lifecycle/worker contracts, apply notifications, UI notification contracts, and cross-project optimization helpers.

Additional checks:

- `python -m compileall -q devtool`
- `py_compile` for modified ENV-X01 regression files

Known baseline exclusion is the CORE-G3 explicit nested-config ownership regression described above; it reproduces independently of ENV-X01.

## Next

- Next overlay: `ENV-X02`.
- Remaining in the current ENV gate window before moving to EXEC: 2 overlays (`ENV-X02`, then separate `ENV-G1`).
- Next major scope after this ENV gate window: `EXEC`, beginning with `EXEC-X01`.
- The broader ENV scope resumes later with `ENV-X03/G2` and `ENV-X04/G3` according to the dependency-driven order.
