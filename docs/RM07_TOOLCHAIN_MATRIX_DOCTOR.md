# RM-07 reviewed host profiles, bounded matrix, compiler cache, and read-only doctor

Implementation: `devtool/toolchain_profiles.py`, `actions_graph_export.py`,
`actions_language_providers.py`, `actions_cli.py` and regenerated CORE surfaces.

## Review-only CLI

```sh
devtool -r "$PWD" actions provider-doctor --all --json
devtool -r "$PWD" actions provider-export --target app --workflow ci --devtool-sha "$(git rev-parse HEAD)" --mode multi-job --plan --host-profiles ubuntu-24.04-x64,macos-15-intel
devtool -r "$PWD" actions provider-export --target app --workflow ci --devtool-sha "$(git rev-parse HEAD)" --mode multi-job --plan --cache-locks
```

`--yes` writes a *new* locally reviewed workflow only; `--verify` compares exact bytes. Neither dispatches, pushes or qualifies anything. `--host-profiles` is an explicit comma-delimited selection, bounded to two distinct reviewed cells. Native DAG jobs are **expanded per profile** instead of using an unreviewed GitHub matrix fanout. Dependencies and artifact handoffs are profile-isolated; no cross-OS mixing, no fake wave-barriers. Total emitted jobs may not exceed 48. Default is precisely one Ubuntu 24.04 x86_64 profile. Existing single-job mode remains default and rejects multi-job-only options.

## Explicit reviewed host profiles

| Profile | Host / arch | Hosted compiler | Export state |
| --- | --- | --- | --- |
| `ubuntu-24.04-x64` | Ubuntu 24.04 / x86_64 | Go, Python-shell 3.12 | Allowed |
| `macos-15-intel` | macOS 15 Intel / x86_64 | Go, Python-shell 3.12 | Allowed, unqualified |
| `windows-2022-x64` | Windows 2022 / x86_64 | Declared Go/Python | **Refused**: POSIX RM-06 bridge not ported |
| `termux-android-arm64` | Android/Termux ARM64 | Local only | **Refused**: not a GitHub hosted worker |

Every cell is checked independently with `graph_host_admission()`; `ci_host_requirements`, `ci_host_os`, `ci_host_arch`, root/device/signing requirements and toolchain compatibility can block the selected profile. The hosted runner label is **policy**, not remote availability evidence. The `run-node` bridge separately checks `RUNNER_OS` and `RUNNER_ARCH` when executing inside GitHub Actions.

## Cache policy (opt-in, never a credential cache)

`--cache-locks` enables only an explicitly pinned `actions/cache` Go **compiler build cache** (`~/.cache/go-build` for Linux, `~/Library/Caches/go-build` for macOS). Its full cache key includes the reviewed profile, selected Go version, and SHA-256 digests of tracked, regular, bounded `go.mod` **and** `go.sum` plus other declared tracked Go lock sources. No caller-configurable cache path; no restore prefix; no arbitrary cache key strings. Symlinks, untracked/missing/oversize lock files and unreviewed Go replace directives cause a refusal. The raw dependency file never becomes a shell expression.

Python caches are disabled, even with `--cache-locks`, pending an independently reviewed *dependency* installation policy. Go module download caches, `.git`, source worktrees, SSH, cloud credential folders, `.npmrc`, environment/secret files and packaging/signing outputs are **not** cached. A build-cache hit is neither source attestation nor a test result.

## Doctor

`provider-doctor --all --json` lists reviewed profile states, available local tool executables, local version output with distinct `installed`, `missing` and `unknown`, version requested by each locally eligible Go/Python node, source lockfiles and an explicit safe next action. Probes are local, read-only and bounded, never invoke installers or fetch tools. A local version does *not* establish an authenticated hosted toolchain.

## Explicitly open

- Windows native GitHub hosted shell/runtime/artifact bridge and real Windows job acceptance.
- Native Termux/Android hosted parity is **not** a goal: Termux is a local host, and its capability report does not grant GitHub authority.
- General language/version/architecture matrices beyond reviewed Go/Python, Ubuntu/macOS x86_64.
- Python dependency caching or arbitrary cache path selection, which require stronger pinned-install and secret-path policies.
- RM-06 affected-target pruning: no source ownership graph qualification or safe changed-target plan yet (RM-20 dependency). Full DAG remains required.
- Live runner invocation, authenticated GitHub provenance, external consumers, device, signing, release qualification.

These remain incomplete roadmap promises, not hidden tests or automatic gates. `tests/test_rm07_toolchain_profiles.py` covers profile refusals, matrix dependencies and byte-stability, host mismatch, cache tampering/symlinks/version/source binding, doctor state, and canonical CLI parity.
