# Historical report retirement (Phase C)

At source commit `05a232453fc1cedcab869540700bcd87e1402860` the repository carried 249 root-level historical report/audit Markdown documents in the audited patterns. This change retires **192** documents that had no literal references from non-report tracked source, tests, data, or maintained documentation, then preserves the transitive references of the remaining historical reports. **57** reports are retained pending explicit consumer migration.

This is a source-tree cleanup, **not** historical G3 release requalification or evidence resealing. The deleted report contents remain accessible in Git history, at the parent commit above. No attestation or existing JSON ledger is altered by this change. `devtool.final_seal` and both target seals remain strict structural/operational checks and never imply formal release qualification.

Removal is conservative, based on path-name references in tracked textual files. Dynamic consumers may exist beyond that analysis; exercise the complete test suite before merging. No compatibility CLI alias, IPC version, or Android bridge protocol is removed in Phase C because those may still be consumed by installed clients. They require separate use-site and migration evidence before retirement.

**Required follow-up:** run the 24-shard hosted regression on the new resulting commit, regenerate V3 native Termux evidence (13 lanes), independently verify the current-source candidate, and preserve these unfulfilled real-substrate obligations: `ENV-A06`, `ANDROID-A16`, `DIST-A01`, `DIST-A12`, `DIST-A13`, `TEST-A11`.

## Phase D follow-up (after the verified `f25196a` baseline)

Four additional zero-literal-inbound-reference reports were retired with individual exact-source SHA-256 provenance records. See `docs/HISTORICAL_REPORT_PHASE_D.md` and `docs/HISTORICAL_REPORT_PHASE_D_PROVENANCE.json`. This reduces the previously retained 57 reports to 53; all unresolved external-consumer caveats and six real-substrate obligations remain.
