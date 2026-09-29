# ENV-X03 bounded promise audit — 2026-09-29

## Boundary and stop rule

This audit is bounded to ENV-X03 (full-plan 17/63, ENV 4/7, gate-window 1/2) and the merged ENV-P073..ENV-P088 promises. It does not absorb ENV-G2 real-process qualification or ENV-X04/G3 adaptive/destination/advanced-feature work. The loop stops when no implementation gap owned by ENV-X03 remains and all later-owned proof/work is explicitly exported.

## Pass 1 — implementation-to-promise audit

Owned gaps found and repaired:

1. **Semantic property values were not identity-significant.** Evidence redaction happened before argument hashing, so `-Pfeature=true` and `-Pfeature=false` could share plan/qualification identity. The planner now hashes real in-memory argv while serializing only redacted property evidence.
2. **Launcher/context qualification was too weak.** Context now binds executable realpath/digest, wrapper properties/JAR/distribution SHA, JDK executable/digest, Gradle user home/cache domain, semantic environment digest, trusted context inputs and build-logic identity.
3. **Legacy validation isolation was not explicitly represented.** `validation.split_gradle=true` now carries an `explicit-isolation` typed barrier. Phase names alone do not create Gradle process barriers.
4. **Protected raw arguments could bypass planner ownership.** Project/settings roots, Gradle user home/project cache, init scripts, Java home and JVM heap overrides now fail closed when supplied through raw Gradle argv.
5. **Property-form managed switches were not converged consistently.** `org.gradle.parallel`, caching, VFS, CC, workers and daemon forms now resolve through the same managed policy/conflict path.
6. **Telemetry/output collection was insufficiently bounded.** Detailed telemetry now caps per-test events and bounded output traversal; producer-scoped output scanning runs only for declared artifact producer tasks.
7. **Receipts lacked some environment/resource qualification evidence.** Receipts now bind JDK identity plus resource-profile provenance, reason and evidence in addition to requested/optimized/observed task truth and EXO/RER/RUN references.

## Pass 2 — caller and qualification audit

Additional owned gaps found and repaired:

1. **Android instrumentation could bypass the canonical Gradle planner.** Both the project-runner instrumentation action and the dedicated `devtool android instrumentation` CLI now build through the canonical Gradle planner first, then run the device orchestrator with `skip_build=true`.
2. **Build-logic identity could silently truncate.** The bounded build-logic scan now records completeness/overflow. Aggregate substitution fails closed and Configuration Cache falls back (or errors in `required` mode) when the identity set exceeds the qualification bound.
3. **Standard structured telemetry was not actually implemented as a CC-compatible structured adapter.** `standard` now stages a public BuildEvents/BuildService task-completion adapter. `full` retains the bounded legacy detail listener and is explicitly CC-incompatible. OCI embeds and byte-qualifies both adapters. ENV-G2 owns real Gradle miss/hit proof.
4. **Embedded OCI runtime truth went stale after telemetry changes.** OCI manifest, validation hashes, runtime rewrite, inspection probes and tests now bind both standard and full telemetry resources exactly.

## Pass 3 — bounded re-audit

No remaining ENV-X03-owned implementation gap was found against ENV-P073..ENV-P088.

The following are intentionally **not** claimed here:

- real Gradle execution proving the standard BuildEvents adapter compiles/loads and survives Configuration Cache miss -> store -> hit;
- real native-Termux policy/resource behavior and process-count proof;
- qualified affected-project graph, destination-resolved remote/federated receipt convergence, advanced Isolated Projects/parallel-CC/remote-cache qualification, or adaptive performance history/plan UX.

Those remain exported to ENV-G2 and ENV-X04/G3 respectively.

## Qualification evidence

Canonical Devtool-native workflow:

```text
./devtoolw env-x03
5 stages passed
269 tests passed
0 failed
0 skipped
0 Devtool warnings
0 Devtool errors
```

Stages:

- target-local `Devtool.Source Syntax`
- `Roadmap Env X03 Planner`
- `Roadmap Env X03 Callers`
- `Roadmap Env X03 Receipts`
- `Roadmap Env X03 Regression`

One pre-existing raw pytest `ResourceWarning` remains in `test_tsys04_artifact_test_protocol.py`; it is LIFE-owned and is not counted as a Devtool warning. The bounded audit did not widen into that unrelated ownership boundary.

`git diff --check` is clean. Real Gradle/Configuration-Cache process qualification is deliberately deferred to ENV-G2.

## Packaging rebase after first apply attempt

The first audited archive was accidentally packaged from the construction-only desktop mirror for `.devtool.toml`. Its manifest therefore expected the mirror SHA and its payload carried `execution_environment = "desktop-linux"`, while the real post-EXEC-G1 repository correctly retained `native-termux`. No implementation file beyond `.devtool.toml` had source drift.

The replacement archive is rebased on the exact post-EXEC-G1 `.devtool.toml`, keeps `execution_environment = "native-termux"`, and keeps targeted overlay validation enabled through `roadmap-env-x03-artifact`. Full real-Gradle qualification remains owned by ENV-G2. The source-hash guard is preserved; it is not bypassed or weakened.

## Native-Termux qualification repair pass (v5)

The v4 targeted validation reached the real native-Termux workflow and exposed four residual regressions in the 184-test regression node:

1. JVM Gradle launcher planning probed the same selected local Gradle executable twice. The launcher resolver now returns the already-probed effective version so the canonical plan reuses that fact instead of performing a second probe.
2. Two Android tests claimed to exercise the balanced policy without actually requesting balanced policy. They now make `resource_profile=balanced` and the 2-worker validation intent explicit, so the assertions are portable and test the named contract rather than ambient free-memory state.
3. The generic explicit-command Android safe-environment regression hard-coded two workers even though ENV-P084/P085 intentionally make automatic resource policy host-evidence-driven. It now checks consistency between the resolved environment and emitted Gradle/JVM limits, while a new regression separately proves an explicit 2-worker/2-CPU override is honored.

The warning repair from v4 remains effective: native validation reported zero warnings. The bounded repair adds no new roadmap scope and does not weaken `maximum_warnings = 0`.

Qualification after the repair: focused reproductions 5/5 passed; canonical `./devtoolw env-x03` workflow 5 stages / 270 tests passed with zero warnings and zero errors in the disposable qualification mirror. Native-Termux remains the shipped execution environment; ENV-G2 still owns real-Gradle process proof.
## ENV-G2 reconciliation amendment

The later ENV-G2 promise-by-promise reconciliation found one ENV-X03-owned semantic defect that the original bounded audit missed: the planner isolated destructive/effectful roots but collected every non-barrier root into one group and inserted that group before barriers. A request such as `clean, assemble, check` could therefore be rewritten as `assemble+check` followed by `clean`, preserving membership but changing semantics.

ENV-G2 folds the repair into the owning gate candidate. The planner now coalesces only **contiguous barrier-free regions**, flushes them before each typed barrier, and therefore minimizes physical Gradle process count without reordering requested semantic roots. The gate adds both a deterministic planner regression and an authoritative real-process proof (`clean` process followed by a coalesced normal process).

This amendment supersedes the earlier pass-3 statement only with respect to that missed ordering defect; the rest of the ENV-X03 bounded-audit disposition remains unchanged.

