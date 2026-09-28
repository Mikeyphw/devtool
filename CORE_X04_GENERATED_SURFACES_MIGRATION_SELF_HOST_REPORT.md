# CORE-X04 — Generated surfaces, migration, self-hosting and CORE adoption

Status: implementation candidate complete; awaiting application through Devtool.

Overlay position:
- CORE-X04 — gate-window overlay 1 of 1 after CORE-G2; CORE scope overlay 6 of 6.
- Full implementation-plan overlay 10 of 63.
- CORE-G3 remains the deferred final CORE consumer-adoption seal and is not merged into this implementation overlay.

Merge-window decision:
- Inspected CORE-X04, ENV-X01 and ENV-X02 before implementation.
- Do not merge. CORE-X04 closes generated discovery/config/wrapper/RUN adoption; ENV-X01/X02 establish a distinct host/privilege/language-environment authority and need their own ownership/validation boundary.

Ledger promises delivered or materially closed in this overlay:
- CORE-P006 — live argparse command paths are adopted into the canonical registry and generated help/docs/completion/schema surfaces derive from that registry/adoption boundary.
- CORE-P012 — explicit startup/parser, generated-surface, help-filter, completion and 10k-run query budgets are declared and regression-tested.
- CORE-P026 — config schema plus explain/doctor/status/diff surfaces consume the same resolved configuration/provenance model.
- CORE-P036 — wrapper schema-1 to schema-2 migration has first-class check/write tooling and idempotent diagnostics.
- CORE-P037 — real launcher pair installation/update is repository-locked, staged, atomic across both launchers, and restores the first member if the second replacement fails.
- CORE-P038 — wrapper/completion discovery remains seal-checked while canonical completion/docs are generated/parity-verified.
- CORE-P039 — Devtool itself dogfoods a schema-2 repository wrapper and source-aware generated launchers.
- CORE-P059 — normal run/history/storage observation opens RUN V3 without creating catalog state.
- CORE-P060 — legacy run/log/history adoption is an explicit mutation (`runs --migrate-legacy`), separate from read-only planning/status.
- CORE-P061 — bounded repository RunQuery filtering/pagination is exposed through the canonical `runs` frontend.
- CORE-P068 — legacy local run/log/history bytes remain readable and are adopted non-destructively as deterministic RUN V3 private attachments.
- CORE-P069 — storage status classifies known repository-local state/legacy/cache/published/projection families and fails on unknown durable `.devtool` families.
- CORE-P070 — generated-output ownership checking fails on missing, drifted or unclassified generated files.
- CORE-P073 — machine help, config schema, generated completion/reference/manpage and registry parity share one generated-contract digest/check path.
- CORE-P075 — help/config status+diff/run query/storage planning are passive observation surfaces; regression tests assert no repository config/catalog creation.
- CORE-P081 — validation is bounded targeted non-Gradle validation; Gradle validation remains deferred to later authoritative gates.
- CORE-P082 — the overlay includes the before-commit editable-install refresh so later overlays dogfood the new CORE contracts.

Implementation highlights:
- Canonical parser adoption closes full live parser parity without changing the registry wire schema.
- Canonical machine help can be searched without scraping argparse text.
- Static Zsh/Bash/Fish completion, config schema/reference, Markdown reference and generated manpage live under `share/core-generated/` with exact drift ownership.
- The existing rich Zsh compatibility projection remains available and is synchronized with every live root command/long option.
- `config schema`, `config status` and `config diff` expose generated schema and resolved provenance state.
- `wrapper migrate` performs minimal schema-only migration while preserving surrounding TOML/comments.
- `runs` exposes RunQuery, storage status, migration planning and explicit non-destructive legacy adoption.
- Repository storage inventory exports ownership rather than absorbing LIFE/DIST/EXO responsibilities.
- Devtool's own `devtoolw` prefers explicit override, then checked-out source self-hosting, then PATH; it cannot silently substitute an unrelated installed Devtool when the source checkout is runnable.

Bugs discovered and absorbed while implementing CORE-X04:
1. The canonical registry initially covered only a representative subset of the live parser; 357 parser paths were outside parity on the starting snapshot. The adoption boundary now reports zero missing paths.
2. `RunCatalog.open()` created storage during read-only history/status queries. Observation paths now use `create=False` and do not mutate state.
3. CORE-G2's launcher atomic/lock promise was modeled but the real `install_launchers()` path still replaced POSIX/Windows launchers sequentially without a repository lock. The real updater now enforces the promise.
4. The canonical wrapper config field `cfg.wrapper.schema_version` pointed at `[wrapper].schema_version`, while the live contract uses `[wrapper].schema`. Generated schema now points at the actual key.
5. The generated manpage joined lines with literal `\\n` text instead of real newline characters. Regression coverage locks real roff line boundaries.
6. The legacy packaged Zsh completion had drifted from the live parser (`core` plus newly added options were missing), and packaged/runtime strings differed by a trailing newline. The compatibility projection is synchronized again while canonical generated completion becomes the new parity authority.

New bug discovered and exported to its natural existing owner (not fixed in CORE-X04):
- Wrapper/invocation regression replay emits `ResourceWarning` for unclosed repository `run.lock` and scoped `runs/<id>.lock` handles. This is lifecycle/cleanup ownership, so it is added to LIFE-X01 and LIFE-G1 rather than being smuggled into CORE-X04. Required future invariants: every success/failure/exception/cancellation path closes both handles; released locks cannot remain observable as live resources; regression runs under ResourceWarning-as-error without leakage.

Validation evidence before packaging:
- Focused CORE-X04/Core-G2/wrapper replay: 60 passed.
- Expanded CORE/wrapper/completion/config replay after drift remediation: 167 passed, 32 subtests passed.
- The two warnings in the expanded replay are the exported LIFE-X01 lock-handle defect above; no CORE-X04 test failed.
- `core surfaces --check share/core-generated --json`: pass; parser command paths 379, semantic registry commands 260, missing parser paths 0.
- `wrapper seal --json`: status `ok`, 29 checks.
- `git diff --check`: pass after normalizing generated Windows launcher text.
- Python compileall: pass.
- Fresh CLI smoke: canonical core help, generated-surface check, wrapper migration check, run migration plan, config status and source-self-hosted `./devtoolw --help` all succeed.

Gate/seal boundary:
- This overlay does not claim CORE-G3. CORE-G3 must later replay the full argv/devtoolw -> canonical identity -> effective config/provenance -> trusted invocation -> RunDescriptor -> private history -> generated surfaces -> query/migration chain through fresh processes and downstream consumers.

Next:
- Next overlay: ENV-X01.
- Remaining implementation overlays in CORE before moving to the next major scope: 0.
- Next major scope: ENV/EXEC foundations, beginning with ENV-X01.
