# LIFE-X02 Canonical Artifact, Trust, and Repository-Pack Model

## Campaign position

- **Overlay:** LIFE-X02
- **Gate window:** implementation 2 of 2 before LIFE-G1
- **Full plan:** 32 of 70
- **Next:** LIFE-G1 (33/70), a separate fresh-process lifecycle proof gate
- **Next major scope after LIFE:** ENV-X05 / Gradle Takeover (34/70)

## Merge-window decision

LIFE-X02 remains standalone. The adjacent LIFE-G1 item is an authoritative proof gate and is not merged into implementation. Cleanup/remediation work belonging to later lifecycle scope is also intentionally excluded.

## Delivered

LIFE-X02 introduces a single canonical artifact lifecycle projection shared by artifact creation/inspection/qualification/apply planning:

- `ArtifactEnvelopeResolver` normalizes overlay, bundle, and snapshot semantics into one envelope and plan model.
- semantic, content, transport, and trust identities are represented separately; schema version does not imply assurance.
- qualification emits a durable receipt and accumulates blockers rather than stopping at the first failure. Unsafe overrides are structured authorizations with provenance.
- resolved plans carry the mutation set, expected final tree, validation contract, write leases, and deterministic bundle last-writer semantics.
- generation ownership and external-effect ledgers establish G0..Gn lifecycle bookkeeping for later execution/recovery gates.
- artifact inspection exports the canonical envelope/qualification/plan projection, and local apply compiles the canonical plan before mutation while preserving existing compatibility such as `--ignore-checksum`.
- `ArtifactExecutionRecord` stores references to lifecycle evidence rather than embedding secrets or large payloads.

The shared trust/signing model adds:

- stable `SigningIdentityId` records and bindings;
- private, non-secret signing identity registry data outside repositories;
- FD/private-file/stdin/keyring/local/external-command secret-provider references;
- certificate continuity checks requiring explicit authorization for changes;
- monotonic trust-policy merging;
- non-secret signing evidence and ephemeral secret staging with cleanup on all exits.

The repository-pack model adds:

- deterministic worktree, tracked/index, commit, and explicit handoff modes;
- multi-repository identity preservation;
- optional verified Git bundles;
- source-stability fingerprints rechecked immediately before publication;
- staging, exact-member verification, and atomic publication;
- deterministic ZIP/TAR/TAR.GZ output;
- pin/retention fields on request, manifest, and receipt;
- rejection of raw `.git`, path traversal, and output publication inside a source repository.

Public CLI surfaces are `delivery plan`, `repo-pack plan/create`, and `signing-identity list/register`. Their JSON output remains inside Devtool's canonical machine envelope (`data`), with an end-to-end regression covering that contract.

## Targeted validation

Local implementation proof before packaging:

- `python -m compileall -q devtool` — PASS
- canonical LIFE-X02 + artifact inspection: **24 passed**
- existing `--ignore-checksum` apply compatibility regression: **1 passed**
- canonical LIFE-X02 suite alone: **19 passed**

The overlay itself owns targeted Devtool validation through the three `roadmap-life-x02-*` test declarations and requires `maximum_warnings = 0`.

## Baseline findings intentionally exported to LIFE-G1/later owners

Two failures reproduced unchanged on the post-LIFE-X01 baseline and are not caused by LIFE-X02:

1. `ArtifactV2Tests.test_bundle_validator_failure_rolls_back_every_overlay` currently retains bundle files under the existing pause/failure behavior; LIFE-G1 must audit authoritative apply/recovery semantics across the whole LIFE window.
2. the Gradle artifact-pipeline init-script expectation is already failing on baseline and belongs to the later Gradle takeover boundary, not LIFE-X02.

## Closure condition

LIFE-X02 closes when this overlay passes its artifact-owned targeted validation with zero warnings/errors and integrates cleanly under normal worktree isolation. LIFE-G1 then audits both LIFE-X01 and LIFE-X02 in a fresh process.
