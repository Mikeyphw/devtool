# DIST-R01 — Candidate-runtime self-refresh remediation

## Why this remediation exists

The DIST-G3 fixed-point audit found a literal promise-completeness defect in `DIST-P100`. The preserved DIST-X01 and DIST-X02 artifacts both require validation, but their artifact manifests have no `finalize` contract and therefore no `before_commit` editable-install refresh. That means DIST-G3 cannot honestly disposition P100 as originally satisfied.

`DIST_R01_HISTORICAL_ARTIFACT_AUDIT.json` records the exact preserved artifact and manifest SHA-256 values and keeps the historical deviation explicit. This overlay does **not** rewrite those artifacts or claim that the missing hook existed.

## Remediation

DIST-R01 restores the campaign invariant at the pre-G3 boundary with the established two-phase Devtool self-host lifecycle:

1. required native-Termux artifact validation starts in the isolated transaction worktree;
2. an explicit `dist-r01-candidate-editable-install` Devtool job runs `python -m pip --disable-pip-version-check install --no-index --no-build-isolation --no-deps -e .` from that transaction root;
3. the next workflow node independently reads installed-distribution `direct_url.json` and requires the editable binding to resolve exactly to the validation checkout root (the transaction execution checkout under canonical `apply-overlay`); child tests are not required to receive Devtool's transaction-control environment variables; all remaining DIST-R01 qualification depends on that proof;
4. after validation succeeds, the `before_commit` finalize hook resolves the stable primary checkout via `DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT`;
5. it verifies whether the installed editable distribution already points at that primary checkout and, if needed, re-points it with `python -m pip install --no-build-isolation --no-deps -e "$ROOT"` under `PIP_NO_INDEX=1`;
6. any pre-existing primary-checkout `devtool.egg-info` metadata is snapshotted/restored so the promotion refresh cannot leave undeclared repository writes.

This deliberately models both identities that the established SSH-02/SSH-03 lifecycle requires: the disposable **candidate runtime used for validation** and the stable **promoted runtime used by the next overlay after transaction integration**. The lifecycle machinery already exists in Devtool; DIST-R01 wires it into the missing DIST boundary rather than adding a second runtime authority.

## Qualification

The `dist-r01` workflow checks the historical deviation record, performs and proves the transaction-candidate editable binding, checks artifact finalize semantics, verifies transaction primary-root/self-refresh behavior, and retains EXO lifecycle-hook convergence. The artifact's own successful `before_commit` hook is the authoritative promotion/rebind action. Because the overlay commit is created only after required validation and this finalizer succeed, a committed DIST-R01 ledger implies both lifecycle halves completed.

## Promise disposition after successful apply

`DIST-P100`: **REMEDIATED_AFTER_HISTORICAL_DEVIATION**. The historical X01/X02 omission remains recorded; the current campaign now proves candidate editable binding during validation and stable-primary rebind before promotion, restoring the complete two-phase self-refresh invariant before DIST-G3.

Next: rebuild and run **DIST-G3 (61/70)** against the qualified DIST-R01 tree.

## Exported upstream issue

Construction preflight also exposed `DEVTOOL-OVERLAY-CHECK-SHA256-FILE-NAMEERROR`: `devtool/overlay/conflicts.py` calls `sha256_file()` on the add-existing path without importing it. Canonical `apply-overlay` remains functional and DIST-R01 passed a disposable lifecycle apply, so this is exported to **CORE/ARTIFACT** and is not folded into DIST remediation. See `DIST_R01_EXPORTED_UPSTREAM_ISSUES.json`.
