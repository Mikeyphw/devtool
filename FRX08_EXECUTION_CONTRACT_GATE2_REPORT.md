# FRX-08 Execution and Contract Gate 2 Report

## Scope

FRX-08 seals the local federation planning/execution contract before remote
execution work begins. It adds a dedicated Gate 2 report/CLI surface and hardens
federated execution against repository identity drift and corrupt EXO lineage.

## Delivered

- `devtool federation gate2` / `execution-preflight`, including JSON output.
- `doctor` qualification under `federation/gate2`.
- FRX-04 Gate 1 prerequisite enforcement.
- Deterministic configured-workflow graph/identity verification.
- In-memory parent/child EXO authority probe with corrupt-lineage rejection.
- Pre-execution child repository identity/root revalidation.
- Post-execution authoritative child snapshot verification for repository
  identity, parent run id, and child run id.
- Standalone compatibility when `[federation]` is absent.

## Validation policy

This is FRX validation gate 2. The overlay requires Devtool validation and does
not use `--no-validate`. Its validation script runs focused FRX boundary/graph
tests, WRAP/TLJ contract tests, and EXO lineage/store tests through the Devtool
`validate --task test` surface, followed by Python compilation checks.

## Local audit

- FRX-01..08 + WRAP/TLJ/EXO compatibility slice: 122 passed.
- Focused FRX-06/07/08 execution-contract slice: 20 passed.
- Touched modules compile successfully.

FRX-08 remains local-provider focused. Remote repository path/identity transport
is intentionally deferred to FRX-09.
