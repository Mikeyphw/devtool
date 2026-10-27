# Devtool cumulative improvements append — RMP-01 C02


## RMP-01 / C02 — consumed artifact deletion authority (2026-10-17)

- **Source/impact:** `devtool/artifact_cleanup_worker.py`, `devtool/cli.py`, `devtool/artifacts.py`, `tests/test_artifact_matching.py`; affects every project using `apply-overlay` source deletion including Termux self-updates.
- **Evidence:** direct worker CLI accepted a forged `--reason "after successful apply"` and deleted an unconsumed external ZIP; a symlinked artifact caused its target to be unlinked. Reproduced only under disposable temporary paths. The original duplicate-family apply test also confirmed implicit sibling deletion.
- **Recommendation/implementation candidate:** restrict the worker CLI to bounded parent-issued source identities, preserve identity through directory-FD unlink, reject symlinks, avoid implicit sibling/sidecar cleanup, require an apply operation, and retain LIFE evidence. Add fail-closed negatives without weakening existing repository cleanup policies.
- **Status:** CANDIDATE / SNAPSHOT-VERIFIED, not installed, not terminally qualified. 48 focused tests pass. Closure blocked on CLI original-path provenance, durable operation proof and real Termux adoption. Source-bound overlay and report: `docs/RMP_C02_SOURCE_BOUND_CLEANUP.md`.
- **Campaign boundary:** original 308 historical RMP entries and six separate RS obligations remain open/unchanged. Defer Actions UX until H0 remediation.
