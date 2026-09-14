# Overlay 15 of 15 — Devtool target final qualification seal

This report closes the `devtool` workspace target as its own final overlay. The target-scoped seal is intentionally separate from the Android HUD overlay so each configured Devtool target can be applied, committed, and validated independently.

## Target

- Target: `devtool`
- Runner: `python`
- Platform: `server`
- Root: `.`
- Apply command target: `--target devtool`

## Delivered contract

The overlay adds `devtool.target_seal`, a deterministic side-effect-free target qualification report used by `devtool final-seal --target <name>`. For the Python `devtool` target it verifies the canonical target identity, repository-local root, Python/runtime final-seal files, release qualification scripts, documentation, target report, and target-specific regression tests.

## Acceptance

The `devtool` target final gate is satisfied when all of these pass in the same target-scoped overlay application:

1. `devtool final-seal --target devtool --strict` reports zero failed target checks.
2. `tests/test_rem15_devtool_target_final_seal.py` verifies the Python target seal and CLI JSON/human surfaces.
3. `devtool final-seal --strict` remains compatible with the existing repository-wide FS03 report.
4. The overlay is applied and committed with `--target devtool`, leaving the Android HUD final seal to its own target overlay.

## Local evidence

The generated overlay was checked with Python compilation and focused pytest coverage for the new target-seal module, the CLI target option, and the unchanged FS03 repository seal compatibility.

## Hotfix v2 promise recheck

The post-seal promise audit found that `final_seal_contracts` was declarative but not yet converted into a runnable validation `CommandSpec`. This target report is revised by hotfix v2: the Python/root `devtool` target now plans a `python-final-seal` step whenever the `final-seal` validation profile is active.

Expected command evidence remains:

```bash
devtool final-seal --target devtool --strict
```

The repository-wide coverage manifest is also upgraded to schema v2 so every one of the 308 finding IDs is listed exactly once, not only summarized by total count.
