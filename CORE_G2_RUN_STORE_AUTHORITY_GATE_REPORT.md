# CORE-G2 — RUN V3 and wrapper authority gate

## Overlay position

- Current gate window: overlay 2 of 2 (`CORE-X03`, `CORE-G2`).
- CORE scope: overlay 5 of 6.
- Full implementation plan: overlay 9 of 63 in the V18 dependency order.

## Gate audit loop

CORE-G2 audited every implementation overlay since CORE-G1:

1. `CORE-X03 — RUN V3 streams, attachments, privacy, query/retention + wrapper qualification`

Audit pass 1 found four concrete gaps in the CORE-X03 promises:

1. Process stream descriptors exposed private disk paths in public/query projections.
2. Invalid stream kinds were rejected only after a stream file could already be written.
3. Tombstoned attachments could still satisfy an attachment-kind query with an empty attachment list.
4. Wrapper plan qualification proved required inputs but did not explicitly prove provided values/secrets are excluded from the machine envelope.

The gate candidate folds those fixes into `devtool/run_store.py`, then proves them in `tests/test_core_g2_run_store_authority_gate.py`. Audit pass 2 is clean.

## Ledger promises delivered by this overlay

- Private stream paths are redacted from public RUN projections by default.
- Private stream paths remain available only in explicit private projections and persisted private catalog storage.
- Invalid stream kinds fail before disk writes, preventing partial invalid raw-output artifacts.
- Tombstoned attachments no longer satisfy public attachment-kind queries.
- Report, test, and evidence outputs are represented as RUN attachments and queryable through the RUN catalog rather than a parallel history database.
- Remote attachment metadata remains lazy and GC-safe without materialization.
- Wrapper plan machine envelopes do not serialize provided input values or secrets.
- CORE-X03 run-store/wrapper promises remain covered by regression tests.

## Validation evidence

Local targeted validation covered:

- `tests/test_core_g2_run_store_authority_gate.py`
- `tests/test_core_x03_run_store.py`
- `tests/test_core_g1_identity_authority_gate.py`
- `tests/test_core_x02_runtime_envelope.py`
- `tests/test_core_x01_registry.py`
- `tests/test_test_g2_lane_selection_gate.py`
- `tests/test_test_x02_inventory_selection.py`

No Gradle validation is required for this Python-only CORE gate boundary.

## Next

- Next overlay: `CORE-X04`.
- Remaining in CORE before next major scope: 1 overlay (`CORE-X04`).
- Next major scope after CORE: `ENV/EXEC foundations`, beginning with `ENV-X01`.
