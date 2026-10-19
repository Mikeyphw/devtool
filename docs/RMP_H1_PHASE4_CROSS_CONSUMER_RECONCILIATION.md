# RMP-H1 Phase 4 — Cross-consumer C05/C06 hardening

**Scope:** source-bound Devtool presentation dashboard and direnv identity consumers.
This is a **security repair and local acceptance milestone**, not formal closure.

- **C05:** Dashboard aggregation must consider every child projection before
  display truncation. A `passed` projection cannot hide another projection's
  `blocked`, `cancelled`, `unknown`, `skipped` or failed status. Unknown status
  vocabulary defaults to `unknown` rather than silently producing success.
- **C06:** The direnv exported-variable fingerprint must exclude credential
  **values**, including their hashes. The nested `status --json` configuration
  identity must exclude values for credential-named fields at every depth.
  Nonsecret export/config values retain meaningful identity changes. These
  adjustments cover identity composition, not generic secrets in arbitrary
  logs or a cryptographic credential system.

Phase 4 acceptance reuses the committed Phase 3 source/tree and native receipt
and runs only relevant H1/PRES/ENV consumer regressions. Requalification of
historical CI24, G3R, Android, SSH and RS13 campaigns is prohibited by scope.

**Remaining H1 obligations:** N01 retained 308-row anchor is not independently
attested; N02 historical persisted journal substrates, C04 remote TEST-A11
selection identities, C05 actual Android HUD projection, and C06 downstream
execution identity propagation still require independent evidence as applicable.
Formal closure flags for N01/N02/C04/C05/C06 remain false. Four H0 findings
remain closed; the six original release/audit obligations stay open.
