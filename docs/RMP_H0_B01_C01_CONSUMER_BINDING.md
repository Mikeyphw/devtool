# RMP H0 — B01/C01 consumer binding remediation

**Implementation only — formal findings remain open.** 2026-10-18.

- **B01: G3 stale evidence / attestation.** The independent G3R v3 verifier now verifies the exact per-lane log filename used by the native producer, requires distinct log references, and rejects empty PASS logs or invalid PASS exit-code types. This prevents a prior lane's log receipt from being recycled under a different required lane. It does **not** claim a cryptographic attestation of worker execution, requalification of historical native G3R, or formal release qualification.
- **C01: false-positive Android completeness.** The release orchestrator now rejects result sequences with missing, duplicate, substituted or reordered command names (while preserving one-command historical phase-name aliases), and rejects non-dry-run skipped required commands. A single result count is not proof that the planned commands executed.
- **Acceptance:** `python3 -m devtool.rmp_h0_acceptance --repo "$PWD"` records exact source and lane hashes and a current local result without rerunning CI24, Android device, SSH or historical native G3R. The receipt explicitly leaves both findings formally open.
- **Risk boundaries:** Tests use synthetic Android release results and G3R log fixtures. Actual native Android execution, current native G3R 13-lane regeneration and independent formal closure remain separately scoped. Preserve qualified historical f25196a and C02/C03 closure provenance; no resealing of older proof.
