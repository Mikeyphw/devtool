# DIST-X01 — Delegation and external-ingest integrity

**Campaign position:** 40/70  
**DIST gate window:** implementation 1/2; window position 1/3 including DIST-G1  
**Next:** DIST-X02 — 41/70  
**Gate:** DIST-G1 — 42/70  
**Next major scope after gate:** LIFE-X03 — 43/70

## Merge-window decision

The next-three positions are DIST-X01 → DIST-X02 → DIST-G1. They remain separate. X01 owns delegation/external-ingest identity and integrity; X02 owns asynchronous delivery/cursor/recovery; G1 stays a separate authoritative milestone.

## Delivered architecture

DIST-X01 extends existing EXO/RUN/federation truth rather than adding a nested scheduler or history store. `ExecutionRunLink` and `DelegationLink` provide deterministic parent-run/parent-node/child-run causality while consuming the existing repository fingerprint, frozen source-lock/equivalence digests, GIT-FRX lineage digest, scheduler provider identity and repository attestation. Absolute checkout paths are not delegation identity.

`EventTransportEnvelope` wraps an already-canonical child event without rewriting its `run_id`, `event_id` or origin `sequence`. Canonical payload SHA-256 is over the event payload only; forwarding provenance remains in the separate envelope.

`ExternalEventIngestor` is distinct from normal local `publish()` and from recovery replay. It authorizes only registered planned delegation/provider/attestation boundaries, enforces one delegation/producer authority per child run, validates origin event identity, accepts an identical duplicate idempotently, rejects a same-ID/different-body event as equivocation, notifies live listeners once, and keeps recovery imports listener-silent.

The preserved-ingest primitive is implemented at the existing EventBus/ExecutionStore boundary. No second event store exists. Accepted payload digests are retained on the bus so reconnect through a fresh ingestor instance remains idempotent within the process.

## Parallel federation convergence

Default parallel federation now wraps each child in the same DelegationLink used by sequential execution and exports the delegation ID into child lineage. After the child exits, the parent imports the child's durable `execution-events.jsonl` journal into the canonical local ExecutionStore as recovery events, preserving the child's run/event/sequence identities. This makes the canonical child run queryable in the parent process rather than relying on the lossy terminal `execution.json` projection as authority.

Raw `process.output` records are deliberately not duplicated through this semantic import. Raw stream/attachment reference transport is owned by DIST-X02. The old snapshot helper remains only as a compatibility fallback for injected/custom executors; the production default child executor fails closed when it produces neither a queryable child run nor a canonical RUN journal.

## Terminal reconciliation

`reconcile_terminal_truth()` is the first provider-neutral terminal reconciliation primitive. A coherent child terminal snapshot and transport rc converge to one authoritative result. A status/exit contradiction or transport-rc/snapshot contradiction becomes a failed integrity result rather than allowing dependent scheduling to consume contradictory truth.

Federated child result records and parent node metadata now carry the typed delegation link plus terminal reconciliation projection.

## Validation

Devtool-native targeted workflow:

`python -m devtool -r . --execution-environment host invoke roadmap-dist-x01`

Result: **55 passed, 0 failed, 0 skipped; 3/3 stages; 0 warnings; 0 errors.**

Additional retained CORE/EXO/attestation sweep: **59 passed, 0 failed.** This covers the existing execution-event protocol, canonical execution store/RUN authority, federation repository identity and Git-bundle attestation boundaries after adding preserved external ingest.

Gradle-heavy or provider-network validation is intentionally not part of X01. DIST-G1 remains the authoritative milestone after DIST-X02 lands.

## Promise disposition

The detailed machine-readable disposition is `DIST_X01_PROMISE_LEDGER.json`. X01 closes the first-class link, single producer, immutable origin identity, external-ingest idempotence/equivocation, live-vs-recovery distinction, planned provider/attestation authorization, terminal reconciliation, and canonical parallel child import foundations. Async sender/spool, cursor/ack/gap recovery, delivery receipts, safe/redacted transport, raw-stream references, provider adapters and protocol negotiation are exported to DIST-X02. Full sequential/parallel parity and delivery/recovery authority remain for DIST-G1.

This report is implementation evidence, not campaign closure. DIST-X01 closes only after its production overlay applies with required validation on the user's native Termux repository.
