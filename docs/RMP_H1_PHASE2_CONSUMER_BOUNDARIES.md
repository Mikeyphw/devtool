# RMP-H1 Phase 2 — Durable resume and selected-case binding

This supplemental, source-bound overlay follows the 2026-10-18 H1 acceptance (212 passing native tests). It does not rerun or replace that accepted receipt and **does not formally close** any H1 family.

## Findings and repairs

- **N02 — persisted validation plan reader:** The H1 serializer rejected unknown nested plaintext credentials, but `deserialize_spec()` could return tampered/legacy metadata, secret-bearing environment entries, and inline command arguments without applying the serialization invariant. The reader now validates field types and round-trips through `serialize_spec()`; `stored_specs()` refuses malformed entries instead of silently dropping them. Valid opaque secret references remain resumable.
- **C04 — selected case identity:** The H1 shard aggregator validated expected case membership, but the manifest converter verified only counts and uniqueness after receiving a mutable aggregate mapping. It now carries `expected_case_ids` from the aggregator and requires attachment membership to exactly match that selection. Count-preserving substitutions, missing inventories, duplicate or malformed selections fail closed.
- **N01, C05, C06:** Their H1 controls are rechecked by nearby regression tests, but this patch neither changes their implementations nor claims external authority, Android HUD evidence, or downstream cross-project qualification.

## Evidence and remaining formal obligations

`python3 -m devtool.rmp_h1_phase2_acceptance --repo .` verifies the prior native 212-test acceptance receipt against **its own original Git commit** before testing the new source. It checks the new HEAD commit, source and lane hashes, native Termux identity, retained 308-row crosswalk, and a selected multi-consumer regression suite.

Still open: independent N01 authority provenance; exhaustive N02 journal/history consumers and historical data; C04 remote-test producer/manifest correspondence and TEST-A11; C05 actual Android HUD projection; C06 downstream persisted-identity behavior and ENV-A06. The six independent release/audit obligations and all H2 families remain unchanged. `release_qualified=false`.

No automatic push, release, remote worker mutation, device install, or historical campaign requalification is performed.
