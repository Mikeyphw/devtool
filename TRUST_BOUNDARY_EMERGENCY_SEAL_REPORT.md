# Trust-boundary emergency seal — Overlay 1 of 15

This overlay closes the seven high-severity findings assigned to the first final-remediation roadmap slice:
`S05-R2-01`, `S09-03`, `S09-R2-01`, `S09-R2-03`, `S12-R2-03`, `S16-08`, and `S16-09`.

## Changes

- Low-level overlay and overlay-bundle runners now validate artifacts before any repository mutation. The CLI's explicit `--no-manifest-check` escape hatch remains explicit and is propagated to the runner instead of accidental direct-call bypass being possible.
- IPC run mutation is producer-owned. Unregistered peers cannot publish state, superseded producers lose mutation authority immediately, and `remove_run` requires the current producer owner.
- Android root validation-control filtering now rejects `$()` and backtick command substitution even inside double quotes, where the shell still expands it.
- The root bridge rejects shell expansion/control syntax before executing accepted Devtool commands, including command substitution hidden in environment assignments.
- Root bridge run IDs are centrally validated before both run and cancel filesystem access; traversal separators and other unsafe characters are rejected.

## Regression coverage

- `tests/test_trust_boundary_emergency_seal.py` exercises direct-apply rejection, IPC spoof/supersession/removal ownership, root command substitution, and run-ID traversal.
- `RootBridgeSecurityTest` exercises the Android-side command gate with safe generated commands plus double-quoted `$()` and backtick attacks.
- The root bridge remains `sh -n` clean.

This is an intermediate remediation overlay. Full campaign validation remains reserved for Overlay 15 of 15; this overlay carries focused tests so its trust-boundary contracts can be checked independently.
