# FRX-01 — Repository Identity Contract (rebased v2)

Status: implemented
Campaign: FRX
Overlay: `devtool_frx01_repository_identity_contract_v2`
Validation policy: disabled (`--no-validate`)

## Rebase note

The first v1 artifact was generated against the pre-wrapper-reseal `devtool/cli.py` and correctly failed source-integrity validation after WRAP execution-evidence re-seal was applied on-device.

v2 is rebased on snapshot `devtool-20260924-133824-432314.tar.gz`. The FRX CLI deltas were merged into the newer CLI rather than replacing it, preserving the wrapper execution-evidence re-seal.

## Delivered

- first-class canonical `RepositoryIdentity`
- stable repository fingerprint and machine serialization
- owning repository root distinct from requested project root
- Git root/common-dir identity signals where available
- config ownership validation that preserves parent-owned workspace configurations
- fail-closed rejection of descendant/unrelated config borrowing
- repository identity attached to loaded `ProjectConfig`
- repository identity in workflow/spec planning metadata and plan JSON
- repository identity on CLI `RunStarted` metadata
- repository identity persisted in EXO execution state/store snapshots
- additive execution snapshot schema bump from 6 to 7
- architecture documentation and adversarial regression coverage

## Audit loop

Rebased implementation:
- focused FRX/config/WRAP suite: 80 passed
- EXO/workflow compatibility suite: 105 passed
- touched-module `py_compile`: PASS

The overlay remains deliberately limited to FRX-01. Federation schema, child repository registration, and boundary discovery are deferred to FRX-02/FRX-03.
