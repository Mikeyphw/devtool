# FRX-X04 Real-Repository Adoption + Detour Final Re-seal

Date: 2026-09-25
Overlay: `devtool_frx_x04_real_repo_adoption_final_reseal_v1`
Base: exact post-FRX-X03 Devtool source state
Gate: FRX-X04 final detour re-seal (validation required)

## Verdict

PASS at source-freeze time. FRX-X04 closes the FRX target-selection/group-execution detour without changing the original FRX-12 fourteen-area public qualification inventory.

The re-seal is based on a **snapshot-contract-derived** adoption fixture from the 2026-09-24 cross-repository audit. It preserves the audited repository aliases, default targets, named workflow surfaces, hidden non-default workflow surfaces, and the observed federation DAG cardinalities. It does not claim that the compact fixture is a live checkout.

## Adopted cross-repository contracts

The audit-established coordinator contracts are preserved exactly:

- `validate-ready`
  - `mike-svmods::validate`
  - `telegram-speed-hook::validate`
  - `chroot-ctl::validate`
  - `mike-cli-suite::validate`
  - expected graph: **28 nodes**, including the synthetic federation aggregate.
- `build-ready`
  - `telegram-speed-hook::build`
  - `mike-cli-suite::build`
  - expected graph: **5 nodes**.
- `test-ready`
  - `telegram-speed-hook::test`
  - `mike-cli-suite::test`
  - expected graph: **3 nodes**.

All three remain parallel aggregates: the X04 acceptance contract rejects cross-repository dependency edges between child workflow nodes.

Executable groups with the same public names resolve through the normal FRX planner and reproduce the same graph cardinalities.

## Hidden target adoption

FRX-X01 target qualification is exercised against the useful non-default workflow surfaces that the original audit could not address:

- Mike SVMods: `letmehatch::validate`, `eggsinwild::validate`, `roostercontrol::validate`
- chroot-ctl: `chrootctl_root_module::package`
- mike-cli-suite: `xbs::build`, `mike_servers::test`
- scriptexts: `smoke-python::validate`
- xdm: `xdm_modern::package`

The acceptance test verifies that addressing these targets does not rewrite each child repository's configured default target.

## Gate/seal remediation

Two material gaps were found during X04 implementation:

1. FRX-08 Gate 2 qualified configured federation workflows but did not qualify executable federation groups.
2. FRX-12 graph planning qualified configured workflows but did not qualify executable groups, so an invalid executable group could escape the final planning seal.

Both are fixed by routing executable groups through `build_federated_group_plan()` inside the existing Gate-2 and final-seal planning contracts.

FRX-12 retains exactly **14 public qualification areas**. X01-X04 promises are bound separately to a machine-readable extension evidence contract:

- target-qualified workflow identity;
- target identity convergence;
- executable federation groups;
- snapshot `validate-ready` 28-node contract;
- snapshot `build-ready` 5-node contract;
- snapshot `test-ready` 3-node contract;
- hidden non-default target surfaces.

Missing or unresolvable extension evidence fails the final seal without creating an accidental fifteenth FRX-12 qualification area.

## Documentation seal

The FRX-12 documentation/completion probe now requires the checked-in architecture contract to contain:

- `FRX-X04`;
- `repository::target::workflow`;
- `federation run --group`;
- the existing FRX-12/federation/final-seal terms.

This prevents the detour from being sealed while its target/group execution contract is undocumented.

## Validation before packaging

- Initial X01-X04 focused gate: **61 passed**.
- Complete FRX restart after implementation: **162 passed**.
- Frozen cross-surface audit restart: **779 passed + 39 subtests**.
- `python -m compileall -q devtool`: **PASS**.

The cross-surface gate covers FRX, WRAP, target-local jobs, EXO lifecycle/reseal compatibility, SSH, chroot, PRoot, OCI, completion/manpage, doctor, TUI/machine/UI surfaces, and GIT-FRX-01 compatibility.

## Audit-loop result

The final source audit restarted from the original detour promises:

1. FRX-X01 — legacy + target-qualified child workflow identity;
2. FRX-X02 — target identity convergence through execution/remote/observability;
3. FRX-X03 — executable federation groups through the normal FRX planner;
4. FRX-X04 — real snapshot-derived adoption, group-aware Gate 2/final seal, and extension evidence.

After the final material fixes (group qualification in Gate 2/final seal and documentation sealing), the full cross-surface audit was rerun from the frozen tree and produced **zero new material gaps**.

## Packaging contract

This is a **validation-gate overlay**. Do **not** apply it with `--no-validate`.

The overlay uses exact source SHA-256 preconditions for every modified file, required rollback-on-failure validation, explicit Devtool `--task test` / `--pytest-test` gates, `compileall`, and the standard before-commit editable-install refresh (`pip install -e ~/Code/devtool`) with packaging metadata restoration.

## Final artifact replay

The first fresh post-X03 replay of the packaged overlay passed:

- Devtool `validate-overlay`: **PASS**;
- exact source preconditions: **5/5**;
- post-apply inventory hashes: **5/5**;
- complete FRX replay: **162/162 passed**;
- `python -m compileall -q devtool`: **PASS**;
- ZIP integrity: **PASS**.

The report was then frozen with this evidence, the overlay manifest/report digest was rebuilt, and the final ZIP was replayed again from a fresh exact post-X03 base before delivery.
