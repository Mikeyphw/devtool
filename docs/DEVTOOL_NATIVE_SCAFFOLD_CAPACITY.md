# Native scaffold capacity alignment for Launcher v3.2.1

Launcher v3.2.1 permits a source-bound 5 MiB individual file and 6 MiB aggregate payload, including the generated ~3.98 MB core CLI schema. Native adoption scaffolding previously refused inputs over 1 MiB/file or 4 MiB total, preventing valid launcher-built delivery packages from reaching native overlay validation.

The fix changes only the two size limits in `devtool/delivery_adoption.py` to match `launcher_contract_v3.py`, retaining bounded plan parsing, secure path validation and symlink rejection, regular-file checks, source SHA/tree binding, no-clobber output, per-file SHA-256 integrity, and native atomic apply/commit authority.

The existing `tests/test_delivery_adoption.py` gains four regressions: equality with the launcher, acceptance of a 4 MiB file, refusal of >5 MiB per file, and refusal of >6 MiB aggregate. All existing negative, rollback, and safety checks remain.

This is a **prerequisite overlay**. Merge its qualified PR before creating a *new* immutable M4+Guardian package pinned to that merged main HEAD. Never modify, repin, reapply or rename the previously built M4 V5 package, and never infer hosted/device acceptance from local tests.
