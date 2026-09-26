# GIT-FRX-11 — Git Notes Execution Evidence

## Scope delivered

GIT-FRX-11 persists federation execution provenance in the dedicated Devtool-owned Git notes ref `refs/notes/devtool` without creating user branches, tags, or commits. Notes consume existing federation/EXO authorities rather than becoming a second run store.

Each final child result note records the parent/child run identities, repository/target/step/contract identity, frozen federation lock and frozen source identities, capability and interface identities (including stable digests), artifact input/output digests, resume/reuse lifecycle, scheduler placement/wave evidence, and parent/child Git lineage.

Writes are append-only and concurrency-safe. A temporary private notes ref is updated first, then published with compare-and-swap `git update-ref`. Repeating identical evidence is idempotent; a different payload attempting to reuse the same record identity fails closed instead of rewriting history. Pre-existing non-structured Devtool notes are retained as `legacy_notes` when structured evidence is first added.

## Public surface

- `devtool git notes show [REVISION] [--json]`
- Dedicated evidence ref: `refs/notes/devtool`
- Self-hosted target-local fixture: `git-frx11-notes-fixture`
- First-class test identity: `roadmap-git-frx11`
- Workflow identity: `roadmap-git-frx11`

## Audit evidence

- Merged focused milestone contracts: 15/15 passed after hardening.
- GIT-FRX-01..13 regression family: 155/155 passed.
- Core FRX regression family: 119/119 passed.
- X/Y federation regression family: 113/113 passed.
- SSH-02/03 evidence boundary: 38/38 passed.
- Completion/help contracts: 20 tests + 32 subtests passed.
- Native merged workflow `roadmap-git-frx11-13`: 9/9 stages, 57/57 tests, 0 warnings, 0 errors.
- Final fixture emitted two non-empty SHA-256 execution evidence digests and preserved `automatic_push=false`.

## Promise ledger

- capability/interface identities: delivered with explicit digest evidence;
- frozen federation lock identity: delivered;
- artifact input/output digests: delivered;
- resume/reuse state: delivered;
- scheduler placement/wave evidence: delivered;
- append-only Git notes semantics: delivered and fail-closed under identity conflict;
- no automatic push / no user ref ownership: preserved.
