# DIST-X02 — Async transport, cursor and recovery

**Campaign position:** 41/70  
**DIST gate window:** implementation 2/2; window position 2/3 including DIST-G1  
**Previous:** DIST-X01 — 40/70, qualified on native Termux (`e5c5337b4d2e41318cc0f6c359460f8c`, commit `13fa36e`)  
**Next:** DIST-G1 — 42/70  
**Next major scope after gate:** LIFE-X03 — 43/70

## Merge-window decision

The active next-three window is DIST-X01 → DIST-X02 → DIST-G1. It remains separate. X01 owns delegation/external-ingest identity and integrity; X02 owns asynchronous delivery, cursor/ack/gap recovery and first provider adapters; DIST-G1 remains the authoritative identity/delivery/recovery milestone.

## Architecture delivered

DIST-X02 keeps the **origin canonical RUN journal** as the durable transport spool. It does not add a second execution journal, scheduler or RunStore. `AsyncJournalForwarder` receives only bounded, non-blocking sequence hints from an origin EventBus; provider/network I/O and cursor persistence occur on the asynchronous worker/pump path. If the bounded hint queue overflows, no canonical event is dropped because reconnect/catch-up resumes from the acknowledged sequence in `execution-events.jsonl`.

`OriginRunJournal` converts origin journal entries into one provider-neutral `TransportFrame` stream. Ordinary semantic events reuse the existing compact RUN journal redaction/bounding contract and cross the boundary as `semantic-safe-v1`. `process.output` bytes never enter that channel: they become protected `RawStreamReference` values keyed by immutable RUN/process/stream/event identity. This keeps stdout/stderr retention/history independent from semantic execution truth.

`DelegatedEventReceiver` enforces contiguous origin sequence acknowledgement, detects gaps, accepts identical at-least-once replay, rejects conflicting same-sequence content, and advances the cursor only after the destination accepts the frame. Live and catch-up delivery preserve origin run/event/sequence identity while remaining distinguishable transport provenance.

`DeliveryCursorStore` persists only private transport cursor/health state. `EventDeliveryState` is explicitly secondary to child execution truth. `DelegatedExecutionReceipt` binds the child run, acknowledged origin range, replay/gap state, terminal snapshot digest, provider identity and negotiated protocol without becoming a competing execution status model. Cursor-ahead, cursor-behind, unresolved-gap and terminal rc/snapshot contradictions fail reconciliation.

## Protocol and provider convergence

`TransportProtocolCapabilities` / `negotiate_transport_protocol()` provide explicit protocol/schema/feature negotiation. Required semantic features fail closed when unavailable; optional unknown-safe-event capability differences are visible as degradation rather than silently reinterpreted.

The same provider adapter shape now exists for local subprocess, federation, chroot, PRoot, OCI and SSH state. The first concrete adoption is parallel federation: the production default path live-tails each child's canonical RUN journal into the parent process through the same X01 `DelegationLink` / `ExternalEventIngestor`, then drains/catches up before terminal reconciliation. A bridge failure remains secondary while the canonical child journal/snapshot is available; recovery import remains the fallback authority, not `execution.json`.

Local delegated execution can attach the same `AsyncJournalForwarder` directly to an origin EventBus. Chroot/PRoot/OCI first adapters expose the identical RUN-journal/cursor contract without introducing provider-specific semantic parsers. SSH foreground/detached live adoption, provider session binding, control acknowledgements and hierarchy remain later DIST-X03 ownership.

## Validation

Devtool-native targeted workflow (construction host with explicit host execution override):

`python -m devtool -r . --execution-environment host invoke roadmap-dist-x02`

Result: **134 passed, 0 failed, 0 skipped; 4/4 stages; 0 warnings; 0 errors.**

Stages cover:

1. source syntax;
2. X02 async transport + retained X01 identity/ingest contracts (20 tests);
3. retained federation lineage/parallel/group/cancellation/observability contracts (45 tests);
4. retained chroot, PRoot/OCI lifecycle and SSH history/recovery substrate contracts (69 tests).

Additional bounded retained sweep: **59 passed, 0 failed** across federation resume, hierarchical federation, SSH-01 transport/environment and SSH-03 lifecycle hardening.

A wider OCI-05 repository-execution check exposed four `embedded Android builder resource digest mismatch` failures. The exact four failures reproduce unchanged on the unmodified DIST-X01 construction base, and X02 changes neither OCI image resources nor the Android builder. They are recorded as a pre-existing OCI/Android baseline defect and are not attributed to or remediated inside DIST-X02.

## Promise disposition

The detailed machine-readable disposition is `DIST_X02_PROMISE_LEDGER.json`. X02 closes bounded journal-backed at-least-once forwarding, cursor/ack/gap catch-up, delivery health/receipt separation, secret-safe semantic transport, raw-stream reference separation, protocol negotiation, high-volume backpressure recovery, parallel federation live tail and the first provider-neutral adapter substrate.

DIST-G1 remains responsible for authoritative fresh-process proof of identity/equivocation protection, exactly-once live notification, non-stalling bounded forwarding, exact reconnect catch-up, redaction, terminal cursor/snapshot reconciliation, sequential/parallel semantic parity and primary-failure preservation. Later DIST-X03 retains real SSH/tmux/control/hierarchy/provider-session adoption.

This report is implementation evidence, not campaign closure. DIST-X02 closes only after its production overlay applies with required validation on the user's native Termux repository.
