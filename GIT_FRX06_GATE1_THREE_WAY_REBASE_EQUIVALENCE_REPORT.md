# GIT-FRX-06 — Gate 1: Three-way overlay rebase and patch equivalence

## Outcome

GIT-FRX-06 replaces the legacy source-hash refresh behavior for base-contracted overlays with deterministic patch-equivalence inspection and real three-way rebasing. Legacy schema-v2 overlays without a GIT-FRX-05 base contract retain their compatibility rebase path.

Public surfaces:

- `devtool overlay status <overlay>` classifies `absent`, `fully-present`, `partially-present`, `ambiguous`, or `legacy`.
- `devtool overlay rebase <overlay>` reconstructs the original base from `base_tree`, performs clean three-way content merges where safe, drops already-present paths, refreshes the base contract, and records rebase ancestry/provenance.
- `devtool apply-overlay <overlay> --rebase` applies the effective rebased artifact; a fully-present patch is a successful no-op and does not create a commit.

## Safety and provenance

Patch equivalence records stable Git patch IDs for the source overlay and current touched-path delta. Structural divergence or content conflicts are never guessed: Devtool emits a `.conflicts.zip` evidence artifact containing conflict metadata and base/current/incoming/merge material where available. Rebases preserve `source_artifact_sha256`, source base commit/tree, source patch ID, equivalence state, and the refreshed target base contract.

The apply-verification path now hashes the effective rebased artifact rather than the original source artifact. This was found by the Gate 1 audit and fixed before packaging.

## Native Gate 1 execution contract

Target: `devtool`.

Workflow: `roadmap-gate1`.

New reusable non-test operation: `overlay-rebase-fixture`.

New declared tests:

- `roadmap-git-frx06`
- `roadmap-gate1-regressions`

Gate validation is declarative. The artifact manifest sets `validation.required=true`, disables generic restore/build/test phases, and references the repository-declared Gate 1 test IDs through `validation.tests`. There is no validator shell orchestrator.

The before-commit lifecycle hook is enabled because this is a validation gate. It installs `${DEVTOOL_TRANSACTION_REPO_ROOT}` when a transaction worktree exists and falls back to `~/Code/devtool` only when no transaction root is active. The hook uses `pip install --no-build-isolation -e` so an already-bootstrapped gate does not need to fetch build-backend dependencies. Editable-build metadata is restored after installation so the hook is repository-write-neutral.

## Audit loop

Focused FRX-06/native contract: 14/14 passed before the final end-to-end additions; final FRX-06 suite is 12/12.

Gate core restart from the earliest affected contracts: 185/185 passed across GIT-FRX-01..06, FRX-X01..X04, FRX-Y01, artifact fail-closed, TLJ/EXO workflow contracts, and self-hosted roadmap planning.

Gate regression aggregate: 103/103 passed across overlay architecture, final-seal golden contracts, artifact pipeline/finalize/preflight, EXO roadmap/TLJ final seal, WRAP final seal/reseal, and workflow convergence.

A repository-wide sweep exposed one stale `tests/golden/build-report.json` contract already failing on the untouched FRX-05 baseline. Gate 1 refreshes that golden to the already-authoritative machine schema established by previous EXO/test-count work; the repaired golden contract passes.

Native plan evidence resolves `roadmap-gate1` to 13 nodes. Preflight on the analysis host stops only at the truthful `native-termux` vs `desktop-linux` environment mismatch; runner, target-local jobs, repository roots, and declared tests otherwise resolve normally.

## Gate boundary

This is full validation Gate 1. It seals the transaction/base/rebase foundation through GIT-FRX-06 before the campaign proceeds to federation intelligence work.
