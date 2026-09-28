# CORE-G1 — command/config/runtime identity authority gate

## Scope

CORE-G1 is the standalone milestone gate after CORE-X01 and CORE-X02.  Per the gate audit-loop rule, this gate audits every overlay since TEST-G2:

- CORE-X01 — canonical CLI/config registry and trust boundary
- CORE-X02 — effective config, secret-safe runtime envelope, and initial RUN descriptor

The gate is not merged with CORE-X03.  CORE-X03 remains the next implementation boundary for streams, attachments, privacy, query/retention, and wrapper runtime qualification.

## Audit loop findings folded into this gate

### Gap 1 — alias spelling changed run identity

CORE-X02 preserved the invoked path inside provenance, but it also used that provenance digest as the RunDescriptor command digest.  That meant `apply overlay` and `apply-overlay` lowered to the same command ID but produced different run identities.

Fix folded into CORE-G1:

- added `command_identity_digest()` for semantic command identity;
- `RunDescriptor.command_digest` now depends on command ID + canonical path, not invoked alias spelling or invocation source;
- provenance still records the invoked path through `TrustedInvocationEnvelope`.

### Gap 2 — output/copy preferences changed run identity

CORE-X02 represented output preferences separately through `OutputRequest`, but CLI-runtime construction also fed copy/stdout output preferences into effective config values.  This allowed human/json/copy presentation choices to perturb the run descriptor config digest.

Fix folded into CORE-G1:

- added `config_identity_digest()` for execution-affecting config identity;
- output-only fields under `cfg.cli.output.*` remain in resolved config/provenance but are excluded from `RunDescriptor.config_digest`;
- `OutputRequest.digest` remains separate, so frontends can still observe output/copy policy changes without altering command/config identity.

### Gap 3 — CORE-X02 check registration did not use the canonical TEST-X02 schema

The CORE-X02 check entry used a legacy compact shape (`lane`, `group`, `resource`, `serial`) instead of the durable X02 inventory schema used by TEST-G2.

Fix folded into CORE-G1:

- changed the CORE-X02 check to `core.x02.runtime-envelope` with durable aliases, behavior, lane, budget, contract level, and groups;
- registered `core.g1.identity-authority` as the system-lane gate check;
- updated `roadmap.core.g1` to include CORE-X01, CORE-X02, and CORE-G1 checks.

## Authoritative proofs

The CORE-G1 test proves:

1. alias and canonical forms share semantic run identity;
2. nested command identity is complete and not prefix-truncated;
3. ordinary argv/environment cannot spoof internal provenance;
4. unknown config keys and wrong types fail with stable diagnostics;
5. effective config provenance and identity are deterministic;
6. secret values are absent from machine/run output;
7. human/json/copy output modes do not change run identity;
8. wrapper alias/direct forms lower to the same command/config identity;
9. registered operations receive private RunDescriptor v3 roots;
10. the CORE-X01/X02 promise audit ledger is clean.

## Deferred by design

CORE-G1 does not implement CORE-X03 storage lifecycle work: streams, attachments, report/test/evidence attachment registry, retention, query, migration readers, or wrapper runtime qualification.  Those remain in the next ordered implementation boundary.
