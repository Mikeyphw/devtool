# ACTIONS-G1 source amendment — QW02 dispatch preflight

The original G8 `ACTIONS_G08_QUALIFICATION_MATRIX.json` and frozen 88-row
historical ledger remain **immutable**. The original G1 source contract SHA-256
in that matrix was `86cefd9457bc5ba0675321b37779a42af2a7375ee5b44d5695365a2c90e15867`; it applies to the pre-QW02
`devtool/actions_dispatch.py` implementation, not the audited current one.

- Exact amended source: `devtool/actions_dispatch.py`, SHA-256 `3bbcfb7155e6f69bb5528bbf41d172793b4e0040595a023d3dd483eb36d91c4c`.
- Regression evidence: `tests/test_actions_02_workflow_dispatch.py`, SHA-256 `fbe93a995071c5eb78c6e09adc4c6ecf18e9f6c2fcb5a594518359272ad7040d`.
- Source adoption: D01/QW02 change committed at `fe3e215430908de727618ea0bcba3ed92a75487f`; carried unchanged through QW03 `b4154f5be97f466149bf37cf7b37b44e72951b1f`.
- Safety property: `expected_source_sha` must be checked before non-idempotent POST and before journaling that a dispatch could have happened. Mismatches are confirmed preflight failures; accepted 204 and transport uncertainty remain sticky and nonrepeatable.

G8 verifies this exact transition using independent byte-hash pins of the
implementation and supporting regression files. It must reject changed,
missing, or symlinked evidence. Source identity is **not** live acceptance,
release attestation, or permission to erase historical obligations.
