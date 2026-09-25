# DEVTOOL-ROADMAP-00 — Native self-hosted roadmap execution contract

**Status:** implemented in the ROADMAP-00 overlay  
**Campaign role:** non-gate bootstrap before FRX-Y01  
**Target:** `devtool`

## Delivered native surface

ROADMAP-00 establishes the roadmap execution contract directly in `.devtool.toml` instead of adding a custom validation shell framework.

### Target-local non-test job

- `devtool.source-syntax`
- structured argv: `python -m compileall -q devtool tests`
- inputs: `devtool/**`, `tests/**`
- outputs: `.devtool/cache/roadmap-pyc/**`
- isolated bytecode root: `PYTHONPYCACHEPREFIX=.devtool/cache/roadmap-pyc`

### First-class declared tests

- `roadmap-artifact-validation`
- `roadmap-frx-baseline`
- `roadmap-git-frx01`
- `roadmap-workflow-exo-baseline`

Each declaration owns a distinct JUnit path under `.devtool/reports/roadmap/` and references only test files that exist in this repository state.

### Workflows

- `roadmap-smoke` — proves target-local job + first-class test composition.
- `roadmap-frx-y01` — establishes the native entrypoint that FRX-Y01 will extend with its scheduler contract when that implementation lands.
- `roadmap-current` — aggregates only currently implemented roadmap contracts.

The broad existing `validate` lifecycle is unchanged.

## Audit-loop result

The implementation is guarded by `tests/test_devtool_roadmap_native_execution_contract.py`, which audits:

- target-local job identity and truthful write scope;
- exact declared-test set and unique JUnit evidence destinations;
- workflow references and dependency composition;
- JSON jobs/workflows/plan surfaces;
- stable target/job/test/workflow identity;
- scheduler scope reasons and conservative test scheduling truth.

No `git-frx-validate-overlay.sh`, `git-frx-validate-gate.sh`, `frx-y-audit-loop.sh`, or equivalent roadmap validation harness was introduced.

## Replay policy

This overlay is not one of the three full campaign validation gates. The user apply should therefore use `--no-validate`. Construction/replay evidence should use the native ROADMAP-00 surfaces (`jobs`, `workflows`, `plan`, preflight where the execution environment permits, and workflow/test/EXO evidence). A desktop analysis host may truthfully fail `native-termux` preflight; that is not a target-ownership defect.

The overlay keeps the Devtool self-refresh lifecycle hook at `before_commit`; before GIT-FRX-03 it installs `~/Code/devtool`. GIT-FRX-03 must switch that hook to the authoritative transaction worktree root when one exists.
