# Devtool FRX Full-Roadmap Re-seal Report

Date: 2026-09-24
Campaign: FRX-01 through FRX-12
Artifact: `devtool_frx_full_roadmap_reseal_v1.zip`
Authoritative base snapshot: `devtool-20260924-195855-674441.tar.gz`
Base SHA-256: `673af03d116f2ed7e161294075fbea376c8d0860bff5e0f691028b41aa2dcf9c`

## Result

The full federation roadmap was re-audited from FRX-01 after every material remediation. The final pre-package audit pass produced no new material FRX gap.

FRX-12 reports:

- status: `ok`
- qualification areas: exactly 14
- adversarial safety cases: exactly 20
- unresolved safety evidence: 0

Final pre-package validation:

- complete FRX-01 -> FRX-12 acceptance, including the new re-seal regressions: **119 passed**
- selected WRAP + TLJ + EXO + SSH + Termux/chroot + PRoot + OCI + UI/doctor/completion compatibility gate: **404 passed, 39 subtests passed**
- Python compile-all: passed

## Original audit gaps closed

### G1 — first-class repository ownership

`devtool/federation_discovery.py` now resolves a path to its explicit federation repository owner by canonical path and recursive explicit enrollment. Longest enrolled root wins; unenrolled nested Git repositories remain owned by the nearest enrolled ancestor; symlink paths resolve canonically; outside paths have no owner; duplicate canonical ownership fails closed. FRX-11 observability and doctor/UI consumers use this contract.

### G2 — cancellation truth

Workflow and direct federation execution now treat exit 130 and `KeyboardInterrupt` as cancellation. Cancellation is terminal for federation scheduling, active children receive cancellation requests, unscheduled branches become explicitly blocked, parent state is cancelled/130, child-owned rollback state is not rewritten by the parent, and persisted parent/child lineage remains inspectable. The wrapper/TLJ direct bridge follows the same semantics.

### G3 — remote repository attestation

FRX-09 now carries path-independent repository attestation using SHA-256 of the root-owned `.devtool.toml` and/or normalized Git origin identity. Remote federation fails closed when no portable signal exists. SSH, Termux/chroot, PRoot, and SSH-PROot-OCI command providers perform pre-command probes. Whole-run SSH/chroot forwarding is verified again at top-level Devtool config re-entry. Absolute local and remote paths are not treated as repository identity.

The audit additionally covered:

- valid different-path checkout
- wrong mapping
- stale mapping after attestation issuance
- Git-only attestation
- secret-neutral metadata (Git URL is not exposed)
- HOME-relative managed remote roots
- missing/truncated whole-run attestation
- nested child config reloads not inheriting an outer child's attestation

### G4 — executable FRX-12 safety evidence

The original twenty roadmap safety cases now bind to a machine-readable `case -> promise -> executable evidence` contract. FRX-12 fails when required evidence is missing or cannot resolve. The public final-seal inventory remains exactly fourteen roadmap qualification areas; safety-evidence failures are separate qualification failures and are visible in both JSON and human-readable output.

## Material gaps found by the audit loop after tests were already green

The loop found and remediated additional issues beyond the four handoff gaps:

1. explicit ownership recursion initially stopped too early for grandchildren;
2. an attempted safety-evidence check accidentally risked becoming a fifteenth public FRX-12 area;
3. exit 130 could inherit a stale persisted `succeeded`/`failed` child status;
4. shell quoting of managed `~`/`$HOME` remote roots could suppress HOME expansion;
5. whole-run remote forwarding could carry an `AVAILABLE=0` attestation and continue;
6. standalone repositories with no portable remote identity needed to qualify by proving that remote federation fails closed, rather than failing the local final seal itself;
7. inherited outer-child attestation could be incorrectly applied to internal nested child config loads;
8. FRX architecture documentation lagged the re-sealed ownership, cancellation, attestation, and executable-evidence contracts;
9. human-readable final-seal output did not surface safety-evidence failures;
10. Git-only and post-issuance stale-remote attestation needed direct executable coverage.

Each material finding caused the audit to restart from FRX-01.

## Audit-loop closeout

The final loop performed:

1. original FRX roadmap/invariant reconstruction;
2. implementation-path inspection;
3. executable evidence mapping;
4. complete FRX acceptance;
5. source review for test-blind gaps;
6. WRAP/TLJ/EXO/provider consumer validation;
7. FRX-12 machine seal inspection;
8. another FRX-01 restart after the last material change.

That pass found **zero new material implementation gaps**.

## Overlay behavior

The re-seal overlay is validation-required, conflict-fail, atomic, and exact-base. Modified files carry source SHA-256 preconditions from the authoritative 19:58 device snapshot. Its validation hook uses Devtool `validate --task test` with explicit `--pytest-test` selections rather than hiding raw pytest behind the gate. The before-commit finalize hook refreshes the editable Devtool install with `pip install -e ~/Code/devtool` while restoring pre-existing `devtool.egg-info` metadata afterward.
