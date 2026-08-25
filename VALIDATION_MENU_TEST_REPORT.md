# Validation menu corrected-release test report

## Focused gate

```bash
PYTHONPATH=. python -m pytest -q \
  tests/test_validation_interactive_menu.py \
  tests/test_completion_and_manpage.py \
  tests/test_validation_sessions.py \
  tests/test_validation_control_api.py \
  tests/test_cli.py
```

Result:

```text
181 passed, 30 subtests passed
```

## Wider validation release gate

```bash
PYTHONPATH=. python -m pytest -q \
  tests/test_validation_*.py \
  tests/test_android_validation_workbench_phase29_31.py \
  tests/test_phase31_1_validation_completion.py \
  tests/test_phase33_validation_release_seal.py \
  tests/test_completion_and_manpage.py \
  tests/test_cli.py
```

Result:

```text
244 passed, 30 subtests passed
```

## Added correction coverage

- revision guards for continue, retry, and selected phase runs;
- stable pretty/compact JSON command generation;
- global/context recipe round-trip;
- true fzf Ctrl-R action handling and top-level catalog reload;
- real pre-session runner-plan resolution;
- reset-from-phase command generation;
- supported recommended safe-retry profiles;
- numbered fallback pseudo-terminal smoke test;
- complete numbered guided-wizard pseudo-terminal traversal;
- Termux/chroot command parser round-trip;
- authoritative workflow `--profile` parsing with separate `--resource-profile`;
- packaged `vm` alias normalization;
- single `--hud-overlay` generation.

## Packaging gate

- Wheel built successfully with all seven `devtool.interactive` Python modules.
- Isolated installation loaded the packaged validation cockpit.
- `devtool vm --recipe smart --print-command` passed from the isolated wheel.
- Installed revision-guard and stable-JSON generation smoke passed.
