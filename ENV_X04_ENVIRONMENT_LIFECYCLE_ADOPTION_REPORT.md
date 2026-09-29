# ENV-X04 — Environment lifecycle and native-runner adoption

## Position

- Gate-window: implementation overlay 1/1 before the dependency order leaves ENV again.
- ENV scope: 6/7.
- Full implementation plan: 22/63.
- Next overlay: PRES-X01 (23/63). ENV-G3 remains the later final ENV seal and is not merged here.

## Merge-window decision

The next-three window is `ENV-X04 -> PRES-X01 -> PRES-X02`.

No merge was performed. ENV-X04 is the later-owned ENV provider/destination/native-runner planning boundary. PRES-X01 and PRES-X02 are presentation-surface implementation boundaries and must not absorb ENV provider semantics.

## Delivered scope

ENV-X04 closes the later-owned provider lifecycle/destination obligations exported by ENV-X02, ENV-X03, ENV-G2 and EXEC-X04 without taking over EXEC, Android, LIFE, DIST, TEST, RMP or PRES ownership.

Delivered implementation:

- `devtool.environment_lifecycle` typed provider/destination lifecycle contracts.
- Destination-returned Python execution snapshots with secret-safe identity.
- Python recursive source, lock, cache ownership and explain projection.
- Go destination snapshots, module security qualification, CGO/cross-target policy, cache ownership and qualified action evidence.
- asdf destination state with `ASDF_DATA_DIR`, `.tool-versions` and legacy version-file qualification.
- pyenv destination state with `PYENV_ROOT`, `PYENV_VERSION` and `.python-version` qualification.
- Gradle opt-in qualification for Isolated Projects, parallel Configuration Cache and remote build cache.
- Remote/federated Gradle request/receipt convergence checks.
- Bounded Gradle adaptive performance history and plan/explain/doctor projection data.
- ENV-owned native-runner contribution records consumed by EXEC without transferring authority.
- `./devtoolw env-x04` targeted wrapper workflow.

## Ownership boundaries preserved

- EXEC consumes environment/toolchain/cache/source identities but does not own provider semantics.
- Android device proof remains Android-owned.
- Rollback/recovery remains LIFE-owned.
- Transport/event delivery remains DIST-owned.
- Human/TUI/Desktop rendering remains PRES-owned.
- Cross-substrate final disposition remains ENV-G3-owned.

## Validation

Targeted validation is first-class and required:

```text
./devtoolw env-x04
  Job: source syntax
  Test: roadmap-env-x04-environment-lifecycle
  Test: roadmap-env-x04-exec-adoption-regression
```

Construction qualification:

```text
Direct targeted tests: 40 passed, 0 failed, 0 skipped
Artifact inspection: passed
```

The construction workflow is bounded and non-authoritative for final cross-substrate proof. ENV-G3 owns final cross-substrate replay and disposition.
