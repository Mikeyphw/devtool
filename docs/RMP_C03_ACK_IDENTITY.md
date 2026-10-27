# RMP-C03 — Durable acknowledgement context binding

Status: **source-bound remediation candidate; formal C03 closure OPEN**.

## Original finding and reproduction

The retained `V108_12_CANONICAL_RISK_FAMILIES.csv` records RMP-C03 (H0, DIST):
`async_ack_cursor_underbinding`. Its original V89 probe substituted an
acknowledgement at the actual asynchronous journal forwarder API. A matching
sequence with an incorrect delegation/event moved the durable cursor in the
historical implementation. Later R03 safeguards added delegation, child,
sequence and event checks, but left the new sender/receiver reply without a
frame-content, provider-session-context or attestation binding.

Reproduction against the current October 18 source before this change: a
synthetic four-field `DeliveryAck` for a single disposable event advanced the
`AsyncJournalForwarder`'s cursor from zero to one without the receiver having
acknowledged its frame. No external worker or production data were touched.

## Implementation

- Receiver replies now carry the exact `TransportFrame.frame_digest`, a stable
  delegated session-context digest built from the complete `DelegationLink`
  (including run-link, provider evidence and source lock), and the link's
  explicit attestation digest.
- The producer validates every field against the frame and its active link
  before **any** persistent cursor update, including duplicate/replay replies.
  Legacy four-field replies and integer-only replies cannot advance a cursor.
- Decode and pump reject coerced sequence types (bool, float and string) and
  malformed duplicate flags. Ordinary callback errors continue to propagate.
- Context remains stable across a legitimate at-least-once recovery of the
  same delegation; new provenance/source/provider identity changes the binding.

These are *identity and integrity* checks, not signatures. Code that controls
both the trusted link and receiver callback can synthesize values. No claim of
cryptographic peer authentication, remote trusted execution or new release
qualification is made. No historical DIST or SSH attestations were replayed.

## Source-bound evidence and status

- `tests/test_rmp_c03_ack_context.py` includes substitution, type, replay,
  serialization and provider-context cases using disposable RUN journals.
- Existing R03, DIST-X01–X04 and DIST-G1–G3 tests remain part of the focused
  compatibility matrix.
- Offline qualification on snapshot: **89 focused DIST tests PASS**.
- `python3 -m devtool.rmp_c03_acceptance --repo .` records a native-local,
  source-committed SHA-256/test-lane inventory and a JSON verdict. A successful
  local receipt does not automatically close C03.
- Native Devtool artifact apply with `apply.commit` and exact commit-scope proof
  is required before claiming installed/source-qualified. Formal closure has a
  separate subsequent evidence gate; RMP-C02 remains closed independently.

## Preserved guardrails

No automatic push, release, requalification of historical campaigns, or
mutation of production workers/data. RMP-B01, RMP-C01, RMP-C03 and all
unrelated obligations remain open pending their own evidence.
