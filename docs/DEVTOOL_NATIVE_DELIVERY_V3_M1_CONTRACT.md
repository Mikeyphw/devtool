# Devtool Native Delivery v3 — M1 strict declarative contract

**Scope:** M0 failure correction plus M1 parser, normalization and read-only inspection; no native runtime selection, post-commit automatic delivery orchestration, remote write authorization or CI execution has been added here. The canonical native Devtool transaction/journal/validation/GitHub services retain their roles. This is **not** a release, signed artifact or device acceptance report.

## Root manifest and compatibility

- `schema_version: 3` is recognized for **individual overlay manifests**. A v3 overlay **must** declare `delivery_contract`; only v1/v2 manifest documents without that field retain legacy behavior. A `delivery_contract` on a v1/v2 document is refused rather than ignored.
- Root `base_contract_mode: exact-tree`, 40-character lowercase Git `base_commit` and `base_tree`, enforced canonical GitHub origin and named non-base target branch are mandatory. The existing exact-tree validator remains authoritative for Git verification.
- `inventory` must be `format: 1`, include 1–48 distinct safe relative add/modify/delete paths, and match `touched_paths`, `source_sha256` preimages and `expected_post_sha256` digests without extras or aliases. Native artifact verification still owns filesystem/archive handling.
- `validation.required: true` with a real executable declared task, reference, script, required inline test or enabled phase is mandatory. Static-only and optional-only tests do not qualify the contract. Existing native test/script validation still executes these checks.
- `apply.atomic: true`, `apply.requires_clean_worktree: true` and `apply.commit` with `enabled: true`, `strategy: single` and a bounded nonempty message are required. Competing root commit or duplicate delivery declarations are refused.
- v2 executable bootstrap byte contents remain unchanged; its exact-tree profile can validate schema v3 subject to the independent M1 contract. **Do not directly execute an untrusted PYZ:** M2 must add preverification of untrusted packages before bootstrap execution.
- v3 delivery extension fields are **data only**. They never grant permission to push, create a PR, merge, dispatch a workflow or qualify CI. They do not change current native apply behavior; later milestones must explicitly implement stage execution.

## Required `delivery_contract` schema version 1

The keys below are exact, not advisory. Additional fields or missing required fields are rejected. Scalar values must match the following supported enumerations.

| Object | Required fields | Meaning |
|---|---|---|
| `delivery_contract` | `version`, `runtime`, `session`, `remote`, `ci`, `recovery` | Version must be integer `1` |
| `runtime` | `compatibility`, `resolution`, `verify` | Compatibility minimum semantic version `>=3.2.0`, major `3`; resolution `trusted-verified-compatible`; verify set exactly `commit`, `tree`, `launcher-module-blob`, `cli-schema-blob` |
| `session` | `after_commit`, `bind_to` | `adopt-or-resume`; bind set exactly `artifact-digest`, `transaction-id`, `result-commit`, `branch` |
| `remote` | `base_repository`, `base_branch`, `base_sha`, `push`, `pull_request` | Upstream owner/repo slug, named PR base, exact Git base SHA; push always `requires-explicit-approval` |
| `pull_request` | `mode`, `draft`, `title` | `requires-explicit-approval`, `true`, bounded nonempty title |
| `ci` | `policy_path`, `observe`, `exact_pr_head`, `qualification` | Exactly `config/delivery-required-ci.json`, `on-explicit-request`, `true`, `required-workflows-only` |
| `recovery` | `after_commit`, `unknown_remote_outcome` | `resume-only`, `read-only-reconcile` |

`base_commit` is the source pre-apply SHA; `remote.base_sha` is the upstream PR-base SHA. They are different contracts and are **not** substituted for each other. For same-repository delivery, the source feature branch cannot equal the PR base branch. Cross-repository fork base identities are represented only as separately pinned `remote` fields.

## Inspect / check (read only)

`normalize_delivery_contract(manifest)` validates the v3 shape without doing any Git, filesystem writes, runtime imports, network requests or test execution. It returns `status: declared-not-executed`, `runtime.verified: false`, `session.adopted: false`, `remote.approved: false` and `ci.state: NOT_QUALIFIED` even for a valid declaration. `inspect_delivery_contract` returns the same projection or `status: REFUSED` and a human-readable reason. The existing `validate_artifact` calls normalization and treats contract errors as artifact validation failures. Native `inspect_artifact` exposes that projection; `validate-overlay` and `overlay check` continue to use the canonical verifier.

No missing CI policy is represented as a pass. This stage only confirms the declared, fixed policy path. Loading the actually committed policy and verifying correct upstream checks requires the existing delivery CI authority and later M4 integration.

## M0 regression correction

A required native `validation.tests` entry with an inline executable command was previously omitted from `ArtifactEnvelopeResolver.qualify`'s executable-validation readiness calculation. The canonical verifier accepted the declaration but the lifecycle gate produced `required-validation-unresolved` before apply. The lifecycle qualifier now recognizes required inline command/task declarations; optional-only or empty entries cannot satisfy the gate. A **separate, checksum-bound native validation hook** is included in this consolidated overlay to make it deployable by the older runtime without disabling that gate. The hook validates source syntax and required declarations; the manifest independently requires real pytest execution of the native security and M1 regression suites.

## Evidence / migration boundary

Only the M0 baseline and the M1 data-only contract are implemented in this consolidated package. No v3 native runtime auto-discovery, native apply-to-session coordinator, remote workflow or cutover is claimed. Existing v1/v2 immutable archives remain on their original acceptance contract. Tests cover valid v3 and malformed/missing/unknown fields, runtime major/minor constraints, origin/branch, inventory traversal and hashes, independent base SHAs, explicit approvals, CI policy constants, recovery and required inline validation; existing gates remain intact. See the accompanying roadmap and M0 test matrix.
