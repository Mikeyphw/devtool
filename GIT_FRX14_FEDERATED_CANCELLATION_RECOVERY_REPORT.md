# GIT-FRX-14 — Federated Cancellation and Recovery

Date: 2026-09-25
Status: implemented and qualified

## Scope delivered

GIT-FRX-14 makes parent cancellation a durable federation lifecycle outcome rather than a transient scheduler event. A cancelled parent stops new child scheduling, requests cancellation of active child runs through the existing child cancellation authority, preserves already-succeeded children as reusable, and marks cancelled or never-started descendants invalid for resume/retry.

Recovery evidence remains ownership-correct. The parent records child run/result/rollback references and operational recovery metadata, but never claims to own repository rollback and never performs automatic Git rollback for a child repository. Child repository Git/EXO authorities remain the source of truth.

FRX-Y02 checkpoints now persist the cancellation/recovery lifecycle alongside scheduler state and child placement, so restart/resume can reuse succeeded children while retrying cancelled or pending children without losing the original recovery references.

## Qualification

- Focused merged GIT-FRX-14 / FRX-Y09 / prerequisite contract batch: 52/52 passed.
- All GIT-FRX-01..14 contracts: 157/157 passed.
- Core federation contracts: 83/83 passed.
- Native `roadmap-git-frx14-frx-y09`: 10/10 stages, 90/90 tests, 0 warnings, 0 errors.
- GIT-FRX-14 native fixture verifies reusable succeeded children, invalidated cancelled/pending children, stopped scheduling, and child-owned rollback semantics.

## Authority boundaries

- Parent federation owns scheduling, cancellation intent, checkpointing, resume/retry decisions, and recovery references.
- Child EXO/Git execution remains authoritative for child terminal state and repository recovery/rollback.
- No parent-owned automatic Git rollback was introduced.
