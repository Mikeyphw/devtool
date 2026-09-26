# GIT-FRX-18 + FRX-Y12 — Signing, provenance, shared-library adoption, final reseal — Gate 3

## Scope and authority

This is the final Git/federation Gate 3 milestone from the handoff. GIT-FRX-18 and FRX-Y12 are co-delivered. Existing Git transaction, federation, frozen-lock, scheduler, resume, artifact-edge, remote transport/attestation, campaign, execution-note, audit, rerere, and doctor authorities remain authoritative; the final gate consumes them rather than creating parallel state.

The combined final authority is `devtool.git_final_seal`. The final manifest and verifier bind both Git/federation provenance and the FRX-Y12 platform/adoption identities projected from authoritative execution notes.

## GIT-FRX-18 delivered contracts

### Git SSH signing policy

`[git].signing` is explicit: `off | if-configured | required`.

- `off` never requests a signature.
- `if-configured` signs only when SSH signing and verification are completely configured.
- `required` fails closed before commit/tag creation when verifiable SSH-signing configuration is absent.
- Signed commits use Git's `-S` path and must pass `git verify-commit`.
- Final signed tags must pass `git verify-tag`.
- Devtool never pushes commits or final tags automatically.

### Canonical federated provenance manifest

For every campaign participant, the final manifest binds:

- repository identity;
- frozen lock, equivalence, and repository-source identities;
- start/result commit and final tree;
- branch/integration target;
- canonical `repository::target::contract` identity;
- capability exports/requirements;
- interface-conformance evidence;
- scheduler/provider placement;
- resume/reuse state;
- overlay digests;
- commit policy and amended/superseded commits;
- artifact producer/consumer digests;
- validation/run IDs;
- remote provider, source-transport, and attestation evidence;
- execution-note evidence digest;
- signing verification;
- rollback ref.

The canonical manifest is hashed and persisted with the seal. Optional control-repository tags use `devtool/<campaign>/<generation>` and bind the manifest SHA-256. The manifest, not shared Git ancestry, proves child repository state.

### Self-invalidating seal

Seal creation requires a fresh zero-gap GIT-FRX-01..18 audit and rechecks freshness immediately before persistence. Verification re-resolves participating commits/trees, campaign digest, result/rollback refs, zero-gap freshness, execution-note evidence, remote evidence, signatures, and optional final-tag binding. A source or authoritative-note mutation after sealing makes the seal stale/failing rather than silently valid.

## FRX-Y12 real shared-library adoption/final reseal

The adoption aggregate uses a shared-platform provider, two direct consumers, and a transitive consumer. The final Gate-3 evidence proves:

1. provider change affects direct and transitive consumers;
2. dependency-safe consumers remain parallelizable;
3. failed consumer/publish-barrier behavior does not roll back unrelated repositories (GIT-FRX-10 authority);
4. retry reuses unchanged successful siblings and reruns failed/invalidated branches;
5. provider revision invalidates the frozen plan/source lock and resume assumptions;
6. producer artifact-digest mutation invalidates consumer reuse (FRX-Y08 authority);
7. provider/named-resource budgets prevent unsafe collisions (FRX-Y09 authority);
8. hierarchical lineage/re-entry remains explicit (FRX-Y10 authority);
9. repository bootstrap/sync is explicit and never implicitly owned by SSH runtime (FRX-Y11 authority);
10. the final manifest verifies the exact commit/tree and platform provenance for every adoption participant.

The mandatory X04 real-repository adoption graphs remain a Gate-3 regression: **28 validate nodes / 5 build nodes / 3 test nodes**.

## Required 40-scenario GIT-FRX audit

Scenarios 1–34 remain owned by their established GIT-FRX/FRX authorities. GIT-FRX-18 directly supplies scenarios 35–40: configured signed commit/tag path, required-signing refusal, final child-result manifest verification, post-audit source-drift invalidation, zero-gap final audit, and no-source-change packaging race detection. The final audit restarts from GIT-FRX-01 rather than assuming earlier gates remain sufficient.

## Construction audit evidence

The corrected combined Gate-3 campaign was executed in bounded chunks:

- GIT-FRX-01..08: **122/122 passed**;
- GIT-FRX-09..18: **90/90 passed**;
- FRX-01..12 + full-roadmap reseal: **119/119 passed**;
- FRX-X01..X03: **35/35 passed**;
- FRX-X04 real-repository adoption/reseal: **8/8 passed**;
- FRX-Y01/Y02/Y04..Y07: **64/64 passed**;
- FRX-Y08..Y12: **36/36 passed**;
- SSH/SSHW: **192/192 passed**;
- chroot/PRoot/OCI: **232 passed + 7 subtests**.

**Construction total: 898 passed + 7 subtests, zero material gaps.**

The FRX-Y12 aggregate additionally maps the non-duplicated adoption promises to their exact authoritative GIT-FRX-10, Y08, Y09, Y10, and Y11 regression tests.

## Native Devtool qualification

`roadmap-gate3` is the final first-class native workflow:

1. source syntax job;
2. `test:roadmap-git-frx17` prerequisite/freshness authority;
3. `test:roadmap-git-frx18-final`;
4. `test:roadmap-frx-y12-adoption`;
5. `test:roadmap-workflow-exo-baseline`.

Plan/preflight are healthy under the explicit desktop-host qualification override. Construction invoke completed **5/5 stages, 51/51 tests, 0 warnings, 0 errors**.

`roadmap-current` advances through GIT-FRX-18 and FRX-Y12. No post-Gate-3 Git/federation milestone is silently invented.

## Apply-time Gate-3 policy

Gate 3 must be applied with validation enabled. To avoid repeating Gate 2's multi-minute Termux historical sweep, the artifact references repository-declared test IDs rather than embedding raw validator commands:

- `roadmap-git-frx18-final`;
- `roadmap-frx-y12-adoption`;
- `roadmap-workflow-exo-baseline`.

The complete 898-test + 7-subtest historical campaign remains construction and exact-replay evidence. The before-commit finalizer preserves the transaction lifecycle by repointing the editable Devtool installation at `DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT` with `pip install --no-build-isolation -e` before commit.
