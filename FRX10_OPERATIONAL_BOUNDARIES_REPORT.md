# FRX-10 — Artifacts, Hooks, Validation, and Transactional Boundaries

Status: implemented

FRX-10 makes child operational ownership explicit without introducing a second
artifact, hook, validation, transaction, or cancellation engine.

Implemented:

- additive `federation_owner` metadata on child artifact production events;
- additive `federation_owner` metadata on child apply transactions and archived
  transaction summaries;
- child validation sessions persist the same owner in `apply_context`;
- embedded hook events persist child owner metadata for both dry-run and real
  execution paths;
- federation child reports aggregate references only, not mutable child state;
- artifact registry, transactions, validation sessions, hooks, diagnostics, and
  logs are correlated to the authoritative child EXO run;
- child resume checkpoints bind repository alias/root/fingerprint and Git HEAD;
- resume rejects moved/replaced children and changed revisions;
- orphan child EXO runs can be identified from lineage when the parent snapshot
  is absent;
- parent cancellation forwards a child cancellation request but performs no
  child rollback/cleanup;
- parent validation sessions cannot satisfy child validation ownership.

Compatibility decisions:

- no schema-version bump was required for artifact, validation, transaction, or
  EXO persistence because all ownership fields are additive;
- non-federated execution does not emit `federation_owner`;
- existing transaction roots, rollback mechanics, hook engine, validation
  session engine, registry, runners, and remote providers remain authoritative.

Audit:

- FRX-10 focused operational boundary suite: 7 passed;
- FRX-01→10 + artifacts/transactions/validation/hooks focused compatibility:
  212 passed before final integration probe;
- expanded FRX/WRAP/TLJ/EXO/provider/transaction/artifact/validation/hook slice:
  379 passed, 7 subtests passed;
- Python compile checks: PASS.

FRX-10 is a non-gate overlay and should be applied with `--no-validate`.
