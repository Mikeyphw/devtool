# RMP-01 / C02 Phase 3 — durable cleanup outcome and recovery

## Defect reproduced

A finished overlay apply can spawn the fresh external-source deletion worker. Before this phase, if the worker timed out *after* unlinking a consumed ZIP but *before* persisting the LIFE receipt, the parent reported success solely because the file no longer existed. No authoritative cleanup completion was proven.

## Behavior after remediation

- Bounded external cleanup receipt plan IDs now incorporate the phase-2 exact source-file snapshot (parent device/inode, file device/inode, size and change/modify timestamps), in addition to repository identity and deletion reason. An older receipt for the same pathname cannot authorize a new file.
- The parent accepts an external cleanup's reported success only after verifying the worker persisted a matching LIFE receipt, listing precisely the consumed paths as `applied` with `absent` postconditions; it verifies repository identity, source absence, canonical receipt filename, and the RUN attachment digest when the ambient run ID is present.
- After timeout, the parent checks those same receipts without restarting the worker or touching the source files. A matching receipt produces `cleanup_recovered_from_receipt=true` and status `applied`; missing/mismatched evidence produces `artifact_cleanup_outcome_unknown`, even if the source archive is absent.
- Repository-internal LIFE cleanup continues through the existing full canonical ownership path. Timeout without this external receipt remains **unknown**, not successful.
- A direct same-user caller with arbitrary repository and runtime write privileges remains outside this process-local authority threat boundary; no release or hardware qualification is claimed.

## Evidence and deployment

Source basis: installed Phase-2 file identities (not a new historical baseline). The overlay preflight checks exact phase-2 hashes and regenerates the Devtool `touched-files` Git contract only for that verified, *committed* checkout. It refuses missing commit state or unrelated file drift and never commits automatically.

Offline regression: 58 focused tests including four new negative/recovery cases. Independent on-device installation and acceptance required for final C02 classification; preserve existing G3R/CI24/SSH and Android evidence.
