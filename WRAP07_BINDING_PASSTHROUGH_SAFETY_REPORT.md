# WRAP-07 — Binding and Passthrough Safety

## Scope

Implemented the wrapper safety hardening layer on top of the post-WRAP-06 tree.

This overlay adds:

- static validation for wrapper argv binding lanes;
- fail-closed rejection of repository/global routing controls in `global_argv`;
- fail-closed rejection of target-scope controls in `command_argv`;
- fail-closed rejection of hidden `--internal-*` Devtool provenance/control flags from wrapper bindings;
- fail-closed rejection of raw `--` separator binding;
- runtime raw-passthrough validation for nested separators, internal flags, NULs and newlines;
- runtime binding-value validation before canonical argv construction;
- sensitive-option detection by option name for `secret`, `token`, `password`, `passphrase`, `credential`, `api_key`, `apikey`, and `auth`;
- `sensitive_options` and `redacted_canonical_argv` in `WrapperResolution` and its machine representation;
- human `wrapper resolve` output now displays the redacted canonical argv when redaction is available.

## Intentional boundaries

WRAP-07 does not add:

- generic secret/environment binding;
- shell evaluation;
- new execution semantics;
- launcher changes;
- polished wrapper help/completion.

The resolver still produces the real `canonical_argv` for execution. Redaction is exposed as an additional safe display/provenance representation and used by the human `wrapper resolve` text path.

## Safety model

Wrapper binding lanes now have separate constraints:

- `global_argv` may not bind `--repo`, `-r`, `--config`, hidden `--internal-*` flags, raw separators, or Devtool command tokens.
- `command_argv` may not bind repository/global controls, target selectors (`--target`, `--group`, `--all-targets`), hidden `--internal-*` flags, raw separators, or Devtool command tokens.
- `passthrough_argv` may not bind raw separators or hidden `--internal-*` flags.
- raw passthrough supplied after `--` may not contain another bare `--`, hidden `--internal-*` flags, NULs or newlines.

Values inserted through `{value}` remain ordinary argv values, but are rejected if they contain NUL or newline characters.

## Audit findings and fixes

The audit loop checked these risks:

1. A wrapper option injecting `--repo` before the canonical command.
2. A wrapper option overriding target/group scope selected by the repository contract.
3. A wrapper option spoofing WRAP-04 internal invocation provenance.
4. A wrapper passthrough binding creating nested `--` separators.
5. Raw passthrough smuggling hidden internal flags.
6. Bound values injecting newlines into canonical argv.
7. Human wrapper resolution output leaking token-like option values.
8. Normal Gradle passthrough such as `--stacktrace`, `--info`, and `-Pabi=...` remaining supported.

All findings are covered by `tests/test_wrap07_binding_passthrough_safety.py`.

## Verification

Focused WRAP-07 tests:

```text
5 passed
```

Combined wrapper suite:

```text
55 passed
```

Selected compatibility replay suite:

```text
155 passed
```

Additional EXO/Termux/scheduler slice:

```text
92 passed
```

Static checks:

```text
python -m py_compile devtool/wrapper_config.py devtool/wrapper_resolution.py devtool/cli.py tests/test_wrap07_binding_passthrough_safety.py — PASS
python -m compileall -q devtool — PASS
```

## Validation cadence

WRAP-07 is not a roadmap validation gate. Artifact validation is disabled and the overlay should be applied with `--no-validate`.

The before-commit hook keeps the established editable-install refresh while snapshotting/restoring `devtool.egg-info` to avoid write-boundary drift.
