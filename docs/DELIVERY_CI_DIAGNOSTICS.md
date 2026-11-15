# Delivery S5 — Source-bound CI supervision and diagnostics

This is **Phase 5 of the original Devtool modernization roadmap**, built on
S4's strict event/run registrar and exact-run/attempt evidence. It introduces
no new GitHub mutation control, release approval, or separate source of truth.

`delivery session ci --id SESSION_ID` performs the existing policy-bound
registration, source verification, exact-run/attempt checks, and branch-rule
reconciliation, then records a **bounded diagnostic snapshot** into the
same durable session journal. `delivery session report --id SESSION_ID`
reuses that saved observation without changing remote state.

## Matrix and aggregate

- Every selected run has an exact run ID, attempt, workflow ID, event,
  current status/conclusion, canonical GitHub URL, and observed source SHA.
- A complete applicable required set with source-bound evidence is a CI pass;
  a required failed check is a CI failure. Missing/ambiguous/unverified
  evidence is pending or blocked. The diagnosis never promotes a CI state.
- Classifications distinguish registration timeout, provider outage,
  ambiguous attribution, uncorrelated manual dispatch, trigger/policy
  configuration, conditional/skipped expectation, source/test failure,
  authorization, environment/dependencies, cancellation, timeout, and
  inconclusive jobs. Step-name categories are **hints**, not root-cause proof.
- Source-bound GitHub run IDs and attempts, job and step evidence, and canonical
  job URLs enable exact manual verification. **No rerun/redispatch** occurs.

## Sensitive log handling

For failed required jobs only, S5 may read up to **three** exact-job logs per
observation using the existing GitHub Actions client's no-redirect transport.
Only a one-hop HTTPS signed redirect on GitHub-controlled log hosts is accepted;
provider Authorization is never forwarded to that signed URL. Each response
is limited to 256 KiB and 15 seconds. An oversized, malformed, redirected,
missing, or inaccessible log yields no excerpt.

Only a maximum of six short error-signal lines per job survive filtering,
using Devtool's canonical log redactor plus independent secret, URL,
identifier, and line-length guards. No raw remote log, credential, signed URL,
or HTTP error body is retained. Redacted excerpts are **supplementary** and
never evidence that an absent run succeeded. If a safe excerpt cannot be
obtained, use the printed canonical job URL. Secret or unexpected log lines
may be omitted entirely.

## Runtime and acceptance boundaries

This module runs through canonical Devtool; the Python 3 + Rich launcher is a
thin renderer and restart-safe orchestrator, not a second GitHub client.
The launcher targets `$HOME/Code/devtool`, uses the existing feature branch
and PR #17, and never force-pushes. Remote writes require explicit approval.
CI qualification **never qualifies a release**, device acceptance, signing,
or merger. S5 remains unqualified until this exact overlay is applied and
its new hosted CI is verified.
