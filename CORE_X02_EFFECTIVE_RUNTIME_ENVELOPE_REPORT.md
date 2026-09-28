# CORE-X02 — Effective config, secrets, frontend parity and initial RUN envelope

## Scope

CORE-X02 implements the runtime authority layer that depends on CORE-X01's canonical registry. It does not attempt the CORE-G1 gate; it provides the effective configuration and run-envelope primitives that CORE-G1 can audit.

## Delivered

- `ResolutionContext` for one invocation's argv, repo scope, CLI values, config sources, environment, output request, and interaction policy.
- `ResolvedConfig` with deterministic precedence and per-field provenance.
- semantic configuration digests that are stable across frontend output modes.
- `SecretRef` and secret-safe serialization that keep sensitive values out of normal machine/run payloads.
- `CliProjectScope` with canonical repo root, repository ID, and scope digest.
- `OutputRequest` and `InteractionPolicy` as explicit runtime policy objects.
- `RunDescriptor` schema v3 with a private repository-scoped run root under `.devtool/private/run-v3`.
- `RunEnvelope` tying trusted invocation identity, scope, effective config, output request, interaction policy, and run descriptor.
- `devtool core runtime --json -- <argv>` inspection surface.
- CORE-X02 check registration in `devtool-tests.toml`.

## Deferred to CORE-G1

CORE-G1 remains the authoritative gate for cross-frontend direct/wrapper parity, config/trust milestone audit, and full identity proof across aliases, wrappers, and output modes.
