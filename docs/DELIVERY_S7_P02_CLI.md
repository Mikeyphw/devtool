# Devtool delivery S7 P02 — canonical consumer adoption CLI

This is the second slice of the original S7 migration milestone. It does not
replace S1–S6 delivery sessions, GitHub orchestration, or the P01 scaffold engine.

## Commands

`devtool --repo /path/to/consumer delivery adoption audit --json` is read-only.
`devtool --repo /path/to/consumer delivery adoption scaffold --payload /separate/payload --plan /path/plan.json --output /safe/new.zip --expected-head FULL_SHA` creates only a new Devtool v2 exact-tree-bound ZIP, outside the consumer repository.

The parser authority lives in `devtool/core_cli_schema.json`, the existing
`delivery_cli.py` delegates to P01's `delivery_adoption.py`, and the launcher
remains a thin Python/Rich coordinator. This eliminates duplicate consumer
CLI parsing without a second application engine. Devtool's own `validate-overlay`
and `apply overlay` remain the only archive lifecycle and mutation paths.

The lightweight `example/lightweight` Git fixture is an acceptance harness,
not a claim that an external consumer repository was migrated. Real consumers
must be onboarded with separately pinned HEADs and independent CI evidence.

No automatic consumer writes, remote pushes, PR POST, GitHub dispatch, local
Gradle, merge, release or signing occur in these commands.
The user's retained S7 P01 session remains bound to its own source SHA.
