# FRX-02 Federation Schema and Parser Report

FRX-02 adds the repository-owned federation configuration contract while
preserving standalone repository behavior.

## Delivered

- `FederationConfig`, `FederatedRepositoryConfig`, and `FederationGroup` models.
- Strict schema v1 parser in `devtool/federation_config.py`.
- Explicit `[federation.repositories.*]` enrollment with owner-root-relative
  canonical paths.
- Required versus optional child availability semantics.
- Deterministic canonical-root duplicate detection, including symlink aliases.
- Self-enrollment rejection.
- Strict repository/group names and unknown-field/type rejection.
- Explicit federation groups with validated repository membership.
- Repository-scoped configuration: global Devtool config cannot inject
  federation enrollment.
- `resolve_config_dict()` machine output for the normalized federation contract.
- No implicit nested Git repository enrollment.
- Architecture documentation and adversarial parser coverage.

## Deliberately deferred

FRX-02 does not load child Devtool configs, infer parent/child ownership,
traverse federation graphs, or add federation CLI commands. Those begin in
FRX-03 and later roadmap overlays.

## Validation policy

FRX-02 is not a campaign validation gate and is intended to apply with
`--no-validate`.
