# FRX-03 Discovery and Repository Boundary Resolver Report

## Delivered

- Added an authoritative federated repository resolver over the explicit FRX-02 enrollment schema.
- Added `devtool federation list` and `devtool federation inspect <repository>`.
- Child discovery is strictly enrollment-driven; nested Git repos are never discovered implicitly.
- Child resolution uses only the child root `.devtool.toml` or child-local detected defaults.
- Parent target/config/workspace state is not forwarded into the child.
- Added `allow_parent_config=False` as an isolated config-loading boundary while preserving normal ancestor-config discovery by default.
- Child repository identity, config path, target, runner/platform, relation, and boundary status are serialized for machine consumers.
- Added federation boundary diagnostics to doctor and status machine payloads and concise human status/doctor checks.
- Optional missing repositories remain visible as unavailable; malformed/ambiguous available child boundaries fail closed.

## Audit

- FRX-01/02/03 focused tests: pass.
- WRAP/EXO/workflow/config compatibility slice: pass aside from one pre-existing FRX-01 explicit descendant-config test failure reproduced unchanged on the pre-FRX-03 baseline.
- Symlink, nested child, optional missing child, malformed child, cwd direction, target-isolation, and unregistered nested-repository adversarial cases: pass.
- Exact packaged overlay replay: required before release of the artifact.

## Scope boundary

FRX-03 does not build cross-repository workflow graphs and does not execute child work. FRX-04 is the first boundary validation gate; FRX-05 introduces the federated plan graph.
