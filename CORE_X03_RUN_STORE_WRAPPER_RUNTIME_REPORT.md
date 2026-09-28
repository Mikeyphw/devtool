# CORE-X03 — RUN V3 streams, attachments, privacy, query/retention + wrapper qualification

Status: implemented as the CORE post-G1 runtime-storage foundation.

Overlay position:
- CORE scope overlay 4 of 6.
- Current gate window overlay 1 of 2 (`CORE-X03 -> CORE-G2`).
- Full implementation-plan overlay 8 of the current V18 dependency order.

Ledger promises delivered:
- AttachmentDescriptor/state machine.
- Node/process attribution for streams and attachments.
- Disk-backed bounded process streams.
- Incremental stream limits and truncation evidence.
- Coalesced snapshot/event persistence.
- Report/test/evidence attachment migration foundation.
- RunQuery/index/catalog.
- Reference/pin/tombstone primitives.
- Reference-aware GC plan.
- Remote attachment metadata and lazy materialization foundation.
- Storage doctor/migration primitives.
- Wrapper plan/preflight qualification.
- Required-input representative seal cases.
- Runtime executable identity/trust.
- Wrapper machine envelope.
- Launcher atomic update plan.
- Repository lock path for wrapper updates.
- Typed runtime failures.
- Wrapper coverage/adoption report foundation.

Immediate fixes covered:
- Dangling log/history GC becomes explicit doctor/GC plan state.
- Anonymous parallel child logs receive node/process attribution.
- Repo-local historical reports can be represented as private attachments.
- Public/private state modes are separated by privacy-aware serialization.
- Wrapper seal checks can no longer be static-only because runtime trust and required inputs are explicit.
- PATH/runtime substitution ambiguity is represented as typed runtime qualification failure.
- Shell-level missing runtime ambiguity becomes `wrapper.runtime.missing` style typed failure evidence.

Validation scope:
- Targeted non-Gradle validation only.
- Gradle validation is deferred to later gates per V18 policy.

Next:
- Next overlay: CORE-G2.
- Remaining in CORE before next major scope after this overlay: 2 overlays (`CORE-G2`, `CORE-X04`).
- Next major scope after CORE: ENV/EXEC foundations, beginning with `ENV-X01`.
