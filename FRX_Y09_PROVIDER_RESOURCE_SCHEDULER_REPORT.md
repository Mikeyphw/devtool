# FRX-Y09 — Provider and Resource Aware Federation Scheduling

Date: 2026-09-25
Status: implemented and qualified

## Scope delivered

FRX-Y09 extends the existing FRX-Y01 scheduler rather than creating a second scheduler authority. Federation planning now projects each child target's authoritative execution contract: allowed/preferred execution environments, preferred worker, and named resource requirements.

Provider selection consumes persisted SSH-01/SSH-03 evidence from `ssh env`, `ssh prepare`, and `ssh doctor`. Federation never probes a remote worker independently. The selected provider/profile/environment and evidence digest are persisted in scheduler output, child run lineage, NodeStarted/NodeFinished metadata, Git execution notes, and FRX-Y02 checkpoints.

Remote provider placement is restricted to workflow child contracts, because job/wrapper contracts still execute through their in-process authoritative bridge. This prevents plan/runtime placement drift. Local `auto` remains `auto` until the child execution layer resolves it, preserving the existing execution-environment authority.

The shared scheduler now supports provider and named-resource budgets plus the roadmap resource classes for local Gradle, SSH/OCI Gradle, tablet Gradle, and Android-device contention. Queue reasons explain when a Y01-safe wave is split by Y09 provider/resource budgets.

## Qualification

- Focused merged GIT-FRX-14 / FRX-Y09 / prerequisite contract batch: 52/52 passed.
- X/Y federation contracts through FRX-Y09: 117/117 passed.
- SSH-02/SSH-03/provider boundary: 41/41 passed.
- Config/execution-environment regression batch: 57/57 passed.
- Native `roadmap-git-frx14-frx-y09`: 10/10 stages, 90/90 tests, 0 warnings, 0 errors.
- Native Y09 fixture proves cached SSH evidence, tablet placement, provider/resource serialization, and queue-reason evidence without a federation-owned remote probe.

## Authority boundaries

- Child target configuration is authoritative for allowed execution environments and resource requirements.
- SSH-01/SSH-03 are authoritative for remote worker/toolchain/host qualification evidence.
- FRX-Y01/Y09 own federation placement and scheduling decisions only.
- Child execution remains authoritative for actual execution status and EXO evidence.
