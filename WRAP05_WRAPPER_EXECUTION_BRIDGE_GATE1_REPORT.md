# WRAP-05 — Wrapper Execution Bridge Gate 1

## Scope

Implemented the first executable repository wrapper surface on top of the post-WRAP-04 tree.

This overlay adds:

- `devtool wrapper describe`
- `devtool wrapper describe --json`
- `devtool wrapper resolve [--json] ...`
- `devtool wrapper exec ...`
- local wrapper resolution before SSH/chroot/PRoot delegation
- canonical in-process re-entry through `devtool.cli.main()`
- non-secret WRAP-04 provenance forwarding:
  - `--internal-invocation-source wrapper`
  - `--internal-invocation-command <wrapper-command>`
  - `--internal-wrapper-schema 1`
- forwarding of outer non-semantic execution controls such as `--ssh`, `--chroot`, `--dry-run`, logging, HUD/UI and toolchain controls
- explicit preservation of target/group/action ownership by the repository wrapper contract

## Intentional boundaries

WRAP-05 does **not** add:

- generated `devtoolw` launchers;
- polished wrapper help rendering;
- wrapper completion;
- secret wrapper transport;
- generic environment binding;
- shell evaluation;
- a second execution engine.

`wrapper exec` is intentionally not progress-capable by itself. The canonical command owns the authoritative run, progress lifecycle, reservations, logs, and EXO state.

## Audit findings and fixes

The audit loop checked for these risks:

1. The outer `wrapper` command accidentally becoming a second EXO run.
2. Wrapper execution delegating over SSH/chroot before resolution.
3. Canonical command routing losing outer execution controls.
4. Workflow wrapper commands bypassing `invoke` semantics.
5. Wrapper help/control modes triggering execution.
6. Wrapper raw passthrough being distributed ambiguously.

Fixes made during the loop:

- `wrapper` dispatch now occurs before whole-run SSH/chroot auto-delegation.
- Canonical re-entry receives WRAP-04 provenance tokens before the real command token.
- `wrapper exec` forwards execution-environment controls to the canonical invocation while leaving scope/action selectors to the wrapper contract.
- `wrapper resolve`/`wrapper exec` subparsers avoid consuming wrapper command `--help` as Devtool parser help.

## Verification

Focused tests:

```text
45 passed
```

Compatibility/gate slice:

```text
206 passed
```

Covered suites:

- WRAP-01 repository wrapper config
- WRAP-02 generic invoke/plan parity
- WRAP-03 pure wrapper resolution
- WRAP-04 EXO invocation origin
- WRAP-05 wrapper execution bridge
- target/job/workflow contracts
- workflow runtime
- workspace groups
- safe scheduler plan
- completion state
- EXO reducer/finalization
- Termux provider paths
- Python/chroot environment paths

## Result

Gate 1 is satisfied for the implementation boundary: wrapper execution is an in-process compiler/re-entry layer over Devtool, not a second Devtool.

## V2 gate reproducibility fix

The v1 gate validation surfaced an environment-sensitive inherited test in
`tests/test_termux_native_android_providers.py`. On Termux devices that already
have `$HOME/android-sdk/build-tools/*/apksigner`, the test could observe the real
SDK before its mocked PATH candidate and fail despite WRAP-05 not touching
Android tool resolution.

V2 makes that test hermetic by using a temporary SDK root and temporary Termux
prefix, then verifying the compatibility accessor chooses the Termux prefix
candidate. The production WRAP-05 code is unchanged from v1.
