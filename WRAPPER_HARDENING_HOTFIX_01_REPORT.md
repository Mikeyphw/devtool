# Wrapper hardening hotfix 01 — routing, launcher ownership, redaction, and completion integrity

## Scope

This post-WRAP-09 hotfix closes the first audit-loop defect set without adding the WRAP-04/WRAP-05 execution-evidence reseal planned for the follow-up gate.

Implemented fixes:

- normalize and reject attached Devtool routing-control spellings in wrapper bindings, including `--target=value`, `--group=value`, `--repo=value`, `--config=value`, `-rPATH`, and `-cPATH`;
- apply the same routing-control policy to both global and command binding lanes;
- re-check every binding token after `{value}` expansion so a generic template such as `"{value}"` cannot become a routing control at runtime;
- make `wrapper update` refresh only Devtool-managed drifted launchers unless `--force` is explicit;
- classify launcher symlinks as unsafe and ensure forced replacement unlinks the launcher path instead of following the symlink target;
- keep raw canonical argv only on the in-process `WrapperResolution` execution object while redacting sensitive option values and all argv projections in machine/JSON serialization;
- add the missing zsh `wrapper` command/action completion branch and config-aware wrapper command/option completion;
- synchronize the shipped `completions/_devtool` with `devtool.completion.ZSH_COMPLETION`;
- repair pre-existing zsh completion parity drift for current OCI and SSH parser surfaces discovered during the audit loop.

## Audit loop

### Loop 1 — original five probes

The pre-hotfix audit reproduced five defect classes: attached routing control bypass, unmanaged launcher overwrite, secret leakage in machine resolution, missing wrapper zsh completion, and a final seal that could accept the routing-hijack configuration.

After implementation, an independent adversarial probe returned:

```text
routing_equal_form_rejected: PASS
dynamic_routing_value_rejected: PASS
update_preserves_unmanaged_launcher: PASS
symlink_force_does_not_follow_target: PASS
machine_resolution_redacts_sensitive_value: PASS
seal_payload_redacts_sensitive_value: PASS
completion_generator_matches_shipped: PASS
zsh_completion_has_wrapper_command: PASS
seal_rejects_routing_hijack: PASS
audit: 9/9 passed
```

### Loop 2 — equivalent-form expansion

The second pass expanded the attack surface beyond the original examples and added regression coverage for:

- `--target=...`, `--group=...`, and `--all-targets` in both command/global binding lanes;
- `--repo=...`, `-r...`, `--config=...`, and `-c...`;
- fully dynamic `{value}` tokens expanding to a routing flag;
- unmanaged launcher preservation with explicit forced takeover;
- symlink non-following semantics;
- sensitive defaults in wrapper resolution and final-seal payloads.

### Loop 3 — completion integrity

Because this hotfix changes completion, the audit also ran the repository's parser/completion parity tests. The supplied baseline already failed this suite due to runtime/shipped completion drift plus missing OCI/SSH parser additions. The hotfix repaired those existing inconsistencies rather than carrying them forward.

Post-fix result:

```text
7 passed, 32 subtests passed
```

## Verification

Wrapper roadmap + hotfix + completion parity:

```text
87 passed, 32 subtests passed
```

Compatibility replay:

```text
158 passed
```

Static checks:

```text
python -m py_compile devtool/cli.py devtool/completion.py devtool/wrapper_config.py devtool/wrapper_resolution.py devtool/wrapper_launcher.py tests/test_wrapper_hardening_hotfix.py
python -m compileall -q devtool
PASS
```

## Validation cadence

This is the first remediation hotfix, not the new final gate. Artifact validation remains disabled and should be applied with `--no-validate`. The established before-commit hook refreshes the editable Devtool install with `pip install -e ~/Code/devtool` while restoring pre-existing `devtool.egg-info` metadata afterward.

The follow-up overlay should perform the validation-required wrapper reseal and extend WRAP-09 to verify WRAP-04 invocation-origin persistence and WRAP-05 execution/re-entry evidence.
