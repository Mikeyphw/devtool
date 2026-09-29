# LIFE-X01 Audit-Loop Remediation v5 — Direct Bootstrap Bridge

Date: 2026-09-29  
Campaign position: gate-window implementation 1 of 2; full-plan 31 of 70.

## Why v5 exists

The v4 implementation itself qualified cleanly on the Termux host: 79 tests passed with zero warnings. Its isolated transaction then failed during final integration because the primary checkout already contained the earlier v1 `devtool/transactions.py` change as a preserved dirty tracked path, while v4 also changed that path. The installed pre-v4 integrator correctly refused to overwrite a non-identical dirty path.

This is a bootstrap problem: the transaction-integration fixes contained in v4 cannot protect the transaction that is installing them, because final isolated-worktree integration is still executed by the older Devtool process loaded before the overlay was applied.

## v5 bridge

- The implementation payload is the same LIFE-X01 remediation code as v4.
- This artifact is intended to be applied once with Devtool's supported `--isolation direct` mode. Direct mode retains transaction backup/rollback and exact affected-path accounting, but bypasses the older isolated-worktree rebase/integration boundary that is blocking installation.
- Validation remains Devtool-native and targeted. Instead of recursively launching `./devtoolw life-x01` (which would need a second reservation on the same primary repository during direct apply), the artifact manifest directly references the five canonical LIFE-X01 test declarations. Devtool therefore executes those registered tests through its normal runner without a nested Devtool process.
- The persistent `.devtool.toml` keeps the canonical `life-x01 -> roadmap-life-x01` wrapper workflow unchanged. After this one-time bridge is installed, normal worktree isolation and wrapper validation resume.
- `maximum_warnings = 0` remains enforced.

## Safety properties

Direct bootstrap does not discard the existing dirty state. The transaction snapshots affected files before mutation and restores them on apply/validation failure. Unrelated dirty state, including the current `uv.lock`, is outside the artifact write set and remains preserved. Historical untracked v1 evidence files are deliberately not rewritten by this bridge.

## Validation target

The artifact directly selects these canonical test IDs:

- `roadmap-life-x01-immediate-lifecycle-safety`
- `roadmap-life-x01-transaction-lifecycle-recovery`
- `roadmap-life-x01-transaction-regression`
- `roadmap-life-x01-artifact-preflight-regression`
- `roadmap-life-x01-git-frx13-regression`

The previously qualified boundary is 79 passed, 0 failed, zero warnings. v5 must reproduce the same target boundary before LIFE-X01 can close.

## Prior attempts

- v1 / run `7303e60d876c479e9008e433a7221412`: 11 passed, 2 lock-handle warnings; audit loop remained open.
- v2 / run `b0a2327d0e124bc0a8ff38e902e6d33d`: Termux POSIX-mode/Git-mode semantic mismatch; rollback completed.
- v3 / run `316ce08eee45445eab4a30189b2f7d34`: 74 passed, zero warnings; historical untracked-report collision blocked isolated finalization.
- v4 / run `176d000610534d658fb67e9fd54d3ac0`: stale v3 recovery succeeded; 79 passed, zero warnings; isolated finalization correctly refused the pre-existing dirty tracked `devtool/transactions.py` overlap.

## Next boundary

LIFE-X01 closes only after this direct bootstrap succeeds and normalizes the lifecycle changes into the repository result commit. The next implementation remains LIFE-X02 (32/70), followed by separate authoritative LIFE-G1 (33/70).
