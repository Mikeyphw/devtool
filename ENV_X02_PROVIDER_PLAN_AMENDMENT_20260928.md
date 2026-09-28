# M004 ENV Provider Plan Amendment — asdf + pyenv — 2026-09-28

## Campaign impact

The canonical implementation order remains **63 overlays**. No new program or standalone hotfix is introduced. This amendment expands the existing ENV-X02 / ENV-G1 / ENV-X04 / ENV-G3 ownership chain.

## Extended provider architecture

The canonical environment model now distinguishes four independent concepts:

1. **host/substrate** — HostContext / native/chroot/PRoot/remote capability;
2. **environment overlay** — direnv, only when already authorized;
3. **tool/runtime provider** — mise, asdf, or Python-specific pyenv;
4. **language environment/dependency owner** — Poetry/PDM/Pipenv/Hatch/venv/uv, Go module/workspace state, etc.

A composition provider is not automatically the language runtime provider. A manager may wrap the command for unrelated tools while Python/Go still comes from the system. Runtime ownership is assigned only when the provider actually declares and resolves that runtime (or declares destination ownership to be proven on the destination).

## Existing asdf extension promises

`ENV-P105`–`ENV-P116` remain allocated to asdf provider truth, with `P113` owned by ENV-X04, `P115` by ENV-G1 and `P116` by ENV-G3.

## New pyenv extension promises

`ENV-P117`–`ENV-P127` are defined in `ENV_X02_PYENV_ADDITION_AUDIT_20260928.md`.

## Cross-provider truth promises

- `ENV-P128` — composition-provider identity and Python/Go runtime-provider identity are distinct evidence domains; unrelated managed tools cannot cause false runtime ownership.
- `ENV-P129` — provider plugin IDs and executable IDs are distinct; asdf plugin `golang`, for example, is preserved as plugin provenance while the qualified executable is `go`.
- `ENV-P130` — requested/declarative versions never masquerade as observed active/current versions; local qualification requires provider evidence for the active runtime.
- `ENV-P131` — ENV-G1 proves unrelated-tool non-ownership, plugin/executable mapping, asdf/pyenv `system` pass-through, and provider-declared-but-unresolvable runtime fail-closed behavior.

## Roadmap ownership

### ENV-X02 — implementation

Adds/finishes:

- asdf and pyenv qualifications;
- deterministic auto/explicit ownership;
- plugin-vs-executable identity;
- system-pass-through semantics;
- provider/runtime ownership separation;
- Python/Poetry and Go provider convergence;
- provider executable content identity / post-overlay verification;
- exact active-version evidence rather than requested-version substitution.

### ENV-G1 — authoritative gate

Extends the fresh-process gate with mise/asdf/pyenv ownership and non-ownership fixtures, no-hidden-mutation proof, provider drift/replacement proof, and actual Python/Go runtime evidence.

### ENV-X04 — destination/lifecycle completion

Completes destination-returned asdf/pyenv snapshots, destination manager roots/state, legacy-provider policy, explain/diff/doctor surfaces, and advanced lifecycle/cache ownership.

### ENV-G3 — final ENV seal

Closes the full local/Termux/chroot/PRoot/SSH provider matrix and final promise/source-ledger disposition.
