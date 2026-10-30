# FC08 HOST-R1R22 — Cargo frozen dependency-graph rejection hotfix

**Exact source pin:** GitHub `Mikeyphw/devtool`, `main` commit `2f11fb507c8e21d5b36f8a48d9ef73a80426eda2`, tree `2268aeadddff12ed72d7c29cb90574a0a769c5a4`.

**Observed failure (not a qualification PASS):** [FC08 run 41090982926](https://github.com/Mikeyphw/devtool/actions/runs/41090982926) on the source above: real Linux job `124184751554` failed `FC08_CARGO_STALE_FROZEN_LOCK_ACCEPTED` (new Cargo negative exited zero). DAG, Windows and macOS jobs succeeded; the fail-closed host inventory correctly refused `FAILED_OR_MISSING_GITHUB_HOST_JOB:linux`. The separate same-commit G1 [41090989020](https://github.com/Mikeyphw/devtool/actions/runs/41090989020) passed 24/24 shards, 6,881 tests, 0 failures/errors/skips. The DAG receipt recorded an actual 403 on the read-only GitHub workflow dispatch attempt: a narrow permission-boundary PASS, **not** successful/repeated dispatch lifecycle authority.

## Cause

R1R21 changed only the root package version, then invoked `cargo metadata --no-deps --format-version 1 --frozen`. This command may complete without resolving the dependency graph or demanding an updated lockfile. Its zero exit is correct; the test's expectation was not reliable. Cargo's `--frozen`/`--locked` guarantees are meaningful when the chosen operation needs to update `Cargo.lock`.

## Promise-by-promise status

| Promise | R1R22 change | Evidence / status |
| --- | --- | --- |
| Demand a genuine stale dependency lock | Add a *new local path dependency* to the temporary Rust fixture after a baseline `cargo build --frozen`; then run the same build with `--frozen` and require nonzero. Dependency addition changes the lock graph rather than relying on metadata/no-deps behavior. | **Implemented; source tests PASS; hosted Cargo pending.** |
| Never qualify an unrelated Cargo failure | Require the bounded Cargo diagnostic to explicitly mention a lock file needing an update, in addition to nonzero exit. Reject compiler, network, permission and arbitrary errors. | **Implemented; negative regression coverage.** |
| Preserve lock/source integrity | Capture and compare baseline lock SHA-256, reject mutations, restore the manifest after success or exceptions, remove the owned local path dependency, re-run `cargo build --frozen`, and require unchanged Cargo.lock. Never modify consumer repositories. | **Implemented; positive/negative regression coverage.** |
| Preserve existing host workload coverage | Linux 14 checks (including uv lock, RM-13 ELF ABI and Cargo), DAG 5 (including real read-only HTTP 403), Windows 8, macOS 3. No changes to GitHub workflow permissions, tokens, CI scheduling, Windows or macOS runners. | **Existing checks retained.** |
| Preserve original R1R21 regression suite | Update its three Cargo mock fixtures to the corrected real build contract, *without removing tests*, and add dedicated R1R22 regression cases for zero exit, error cause, lock tamper, cleanup and unexpected dependency fixture. | **139 FC08 pytest PASS in a local owned copy.** |
| Preserve fail-closed final inventory | No summary, receipt schema, gate policy, or release authority changed. A failed Linux job still blocks final summary. | **Source unchanged.** |
| Prove new-host performance and four-lane evidence | Require a fresh real workflow on post-R1R22 commit, Linux 14 checks and DAG 5, Windows 8, macOS 3, and a valid four-lane summary; require same-commit G1 24 shards. | **OPEN** until hosted run. |
| Independent qualification beyond these checks | Native Termux ARM64, Android signer/device, real consumer/BKX R3 and FC09 release, successful/repeated GitHub dispatch, extra lock/ABI cases. | **OPEN; not promoted.** |

### Constraints

No network dependency added: the new crate is an owned local path fixture. Existing root Git metadata, stashes, and user trees must remain untouched by the exact-source overlay unless the transactional Devtool installer approves the change. No `fc08_overall_qualified=true`, `device_qualified=true`, or `release_qualified=true` claims. Passing local mocks is not real Cargo evidence; the next hosted Linux execution must prove the native operation.
