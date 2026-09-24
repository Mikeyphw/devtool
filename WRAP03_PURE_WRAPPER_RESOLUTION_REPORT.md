# WRAP-03 — Pure Wrapper Argument Resolution

## Scope

WRAP-03 adds the execution-free repository wrapper resolver promised by the audited wrapper roadmap.

Delivered:

- `devtool.wrapper_resolution` module
- deterministic `WrapperResolution` dataclass
- `WrapperResolvedOption` option records
- `resolve_wrapper_argv(config, argv, launch_cwd=...)`
- `wrapper_resolution_as_dict(...)` machine representation
- native wrapper command compilation to canonical Devtool argv
- workflow wrapper command compilation to `devtool invoke ACTION`
- repository-root and launch-cwd provenance in the resolution object
- typed parsing for booleans, strings, choices, integers, floats and explicit-base paths
- command aliases and option aliases
- default command handling for option-first invocations
- required options, repeatable options, defaults, conflicts and requirements
- positional and repeatable positional resolution
- token-only `{value}` binding expansion
- `global_argv`, `command_argv`, and `passthrough_argv` expansion
- raw `--` passthrough only when the native wrapper command explicitly opts in
- structural top-level and command-level help/version resolution
- reserved wrapper command/control spelling validation

No execution path was added. The resolver does not call Devtool `main`, runners, workflow planners, subprocesses, shells, or EXO. It only returns data for later WRAP-05 dispatch.

## Architecture

```text
wrapper argv
    |
    v
ProjectConfig.wrapper
    |
    v
resolve_wrapper_argv(...)
    |
    v
WrapperResolution
    |
    +-- native command  -> devtool build/test/validate/... argv
    |
    +-- workflow command -> devtool invoke ACTION argv
```

Native commands remain native. For example:

```bash
./devtoolw build --release --abi arm64-v8a
```

can resolve to canonical argv equivalent to:

```bash
devtool --repo /repo build --target android --variant release -- -Pabi=arm64-v8a
```

A workflow command resolves through WRAP-02's generic invocation surface:

```bash
./devtoolw codegen
```

resolves to:

```bash
devtool --repo /repo invoke codegen --target android
```

## Audit loop

### Pass 1 — implementation boundary

Verified that `wrapper_resolution.py` imports only models and pure Python utilities. It does not import CLI dispatch, runners, workflow runtime, subprocess, shell helpers, EXO, reservations, or progress systems.

### Pass 2 — reserved controls

The initial schema only reserved option spellings such as `--help`, `-h`, and `--version`. The audit loop tightened this so command names and aliases cannot claim `help` or `version`, preventing later UX conflicts when launcher/help rendering is added.

### Pass 3 — path semantics

Path options and positionals now require an explicit base from WRAP-01 validation and are resolved by the pure resolver against either the repository root or launch working directory. Relative path behavior is covered by tests.

### Pass 4 — canonical argv ordering

Resolution separates:

- global argv before the command token;
- native command args after native command tokens;
- workflow args after `invoke ACTION`;
- passthrough args after `--`.

This keeps WRAP-05 able to re-enter normal Devtool CLI semantics without constructing a synthetic namespace.

## Verification

Focused WRAP-03 tests:

- 9 passed

Combined wrapper suite:

- WRAP-01 + WRAP-02 + WRAP-03: 35 passed

Selected compatibility/audit suite:

- 180 passed

Covered areas include:

- WRAP-01 repository wrapper schema
- WRAP-02 generic invoke and plan parity
- target/job/workflow contracts
- workflow graph/runtime
- cross-target workspace groups
- safe scheduler and reservation planning
- completion state
- repository/config discovery
- UI11 machine completion/config surfaces
- Termux/chroot routing contracts

Static checks:

- `python -m py_compile devtool/wrapper_config.py devtool/wrapper_resolution.py` — PASS
- no resolver subprocess/shell/runner imports — PASS
- no execution dispatch introduced — PASS
- no generic secret/environment binding introduced — PASS
- no wrapper CLI command introduced — PASS

## Validation cadence

WRAP-03 is not a roadmap validation gate. The overlay keeps artifact validation disabled and is intended to be applied with `--no-validate`.

The before-commit hook refreshes the installed Devtool with `pip install -e "$HOME/Code/devtool"` while snapshotting/restoring `devtool.egg-info` so setuptools-generated metadata cannot violate Devtool's write boundary.
