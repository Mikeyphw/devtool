# PRES-G1 Textual Replacement Proof Gate

## Scope

PRES-G1 is a gate overlay. It closes the Textual replacement implementation window that began with PRES-X01 and ran through PRES-X07.

It does not introduce a new UI feature. It proves the implemented state:

- Textual is the default TUI backend.
- `auto` prefers Textual.
- legacy TUI is fenced behind explicit selection or permitted missing-Textual fallback.
- `--require-textual` / `--no-tui-fallback` fails closed.
- `--explain-backend` exposes the backend decision before launch.
- current TUI feature parity remains covered.
- machine output remains unchanged.
- Textual owns rendering and interaction only; RUN, EXO, RER, validation and ENV records remain authoritative.

## Gate-window coverage

The gate verifies reports from:

- PRES-X01 presentation projection foundation
- PRES-X02 presentation layout and interaction semantics
- PRES-X03 cross-surface rendering adoption
- PRES-X04 Textual TUI substrate foundation
- PRES-X05 Textual feature parity
- PRES-X06 Textual default replacement
- PRES-X07 Textual replacement hardening

## Validation

Targeted local validation used for artifact construction:

```text
python -m compileall -q devtool tests
python -m pytest -q --tb=short \
  tests/test_pres_g1_textual_replacement_proof_gate.py \
  tests/test_pres_x07_textual_replacement_hardening.py

19 passed
```

The artifact validation contract runs the canonical wrapper workflow:

```text
./devtoolw pres-g1
```

The wrapper workflow runs source syntax, the PRES-G1 proof test, and retained PRES-X07 hardening regression.

## Outcome

PRES-G1 is ready for authoritative apply-time validation on the Termux host.
