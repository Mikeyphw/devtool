# RMP-H1 — Consolidated consumer hardening (2026-10-18)

## Scope and interpretation

This source-bound change covers four reproducible H1 consumer weaknesses and
adds a retained external crosswalk for N01. It **does not** formally close any
H1 family or requalify historical native campaigns.

| Family | Finding / action | Evidence scope |
| --- | --- | --- |
| N01 | Compare 308 `(id, overlay, title)` mappings and exact owner counts against an independently retained 2026-10-17 RMP00 accounting CSV | Retained reconstruction, not an independently signed authoritative roadmap; remains **open** |
| N02 | Reject unknown inline credentials in nested durable metadata even when the secret is not present in the environment | Public plan serializer and focused adverse tests; other persistence readers remain an audit obligation |
| C04 | Bind complete shard manifests to unique case IDs and per-shard terminal outcome counts, reject forged case-less summaries and non-integer totals | In-process test-runtime aggregator and manifest consumer, not proof of a real remote runner or TEST-A11 |
| C05 | Decide presented overall terminal state from **all** EXO events, not only the first rendered page | Machine/TUI/desktop projection-level truth; physical Android HUD end-to-end unqualified |
| C06 | Remove secret-value hashes from semantic environment overlay identity, preserving key presence but not secret rotation | ENV composition identity; all downstream persisted identity consumers remain for qualification |

The pinned N01 CSV is extracted from `DEVTOOL_RMP00_RECONCILIATION_20261017.zip`
(`RMP00_308_FINDING_ACCOUNTING.csv`) and contains only the three canonical
assignment columns. Its byte digest is independently pinned by
`devtool/rmp_h1_acceptance.py`. Its assignment SHA-256 equals the previously
pinned canonical source digest `8857e51e91cc91b31d733c48d081ef661b31360bbc8e590e490c305769b05704`.
A digest pin is **not** a signature, a source authority endorsement, or proof
that 308 historical findings are natively requalified.

## Acceptance

Use `python3 -m devtool.rmp_h1_acceptance --repo "$PWD"` after applying the
source-bound overlay and committing it. The producer verifies the clean Git
HEAD/tree, N01 crosswalk, selected source/test hashes, and focused pytest
results. It writes a unique JSON receipt to `~/.local/state/devtool/rmp-h1/`.
All H1 families remain `formal_h1_closed=false` pending consumer and/or native
qualification. C02/C03 and H0-B01/C01 closure records remain unmodified.

No automatic push, release, live native Android execution, remote SSH worker
mutation or requalification of prior campaigns occurs.
