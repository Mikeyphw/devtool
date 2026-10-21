# RM-00: Stable contracts, provider registry and host admission

Source-bound baseline for this overlay: `343f3dbe55315385baaeeaa0daae8f29f9375bf2`. Planned IDs: DT-017, DT-041, DT-051.

This milestone introduces **contract V2 readers and descriptors**, not a second executor or a new trust authority.

- `devtool.contracts_v2.reviewed_providers()` returns source-reviewed immutable descriptors (ID, V1 adapter API version, capabilities, hosted profiles, safe-argv grammar and artifact roles). Existing Go/Python adapter execution remains V1 and unchanged. Unknown/dynamic providers, unsupported API major versions and unsafe command grammar are refused.
- `negotiate_contract()` requires explicitly supported major versions and feature names; it is a read-only compatibility selector.
- `host_eligibility_v2()` accepts explicit OS, architecture, toolchains, abstract capability tokens and bounded environment assertions. Root, device and signing requirements cannot be satisfied merely by labeling a generic hosted runner. The native graph host-admission check **intersects** typed and legacy policies, never overriding a legacy refusal.
- `read_evidence_v2()` validates V1 typed evidence (or a V2 envelope wrapping it) including source/run/attempt identity, reports the supplied gate claims, and projects independent execution/verification/device/release domains as `UNKNOWN`. This reader cannot grant trust, device qualification or release authority.

## Source and target validation

For every RM overlay, include:

1. Source-bound Git base/HEAD/tree and exact file preimages; fail before applying to drifted or dirty repositories.
2. An explicit `target = "devtool"` artifact declaration and `--target devtool` on the command line.
3. A mandatory artifact validation script that loads Devtool's native config and validation DAG planner **in-process** and verifies exact `devtool` selection and a nonempty valid graph; this avoids a nested CLI process during an active overlay transaction. The installer separately runs the full read-only `plan validate --json` **before** mutation.
4. Focused regressions in the same validation script. A failure blocks overlay validation and rolls back by default.
5. A read-only native target-plan check before mutation, and an in-transaction native graph check with focused regression tests after mutation. Both checks are required.

Target-plan validation is deliberately not a claim that the entire multi-hour repository build/device qualification was run. Full-target validation can be invoked independently with `devtool -r ~/Code/devtool --target devtool validate` when desired.

## Regression surface

`tests/test_rm00_shared_contracts.py`, `tests/test_next02_evidence_kernel.py`, `tests/test_next03_language_providers.py`.

RM-01/02 should use these contracts; do not replace native DAG/EXO or historic evidence. Preserve source-locked old receipts with no status promotion.
