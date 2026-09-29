# Termux SSH Native Preflight Hotfix

## Scope

Fix native-Termux execution preflight when Devtool is invoked from an SSH login session into the same Termux host.

Before this hotfix, `SSH_CONNECTION` caused `detect_host_context()` to classify the active substrate as `ssh`, even when Termux markers proved that the process was running inside native Termux. A target configured with `execution_environment = "native-termux"` therefore failed with:

```text
Execution environment preflight failed (native-termux): termux: ssh
```

## Fix

`HostContext.current_substrate` now treats Termux markers as the execution substrate before considering SSH transport. SSH remains recorded as `remote=True`; it simply no longer replaces `native-termux` when the actual process environment is Termux.

Remote/delegated provider behavior remains protected: non-Termux SSH sessions still resolve to `ssh`, and the existing remote-provider test remains in the validation slice.

## Validation

- Direct targeted pytest: 3 passed.
- Wrapper construction workflow: `./devtoolw termux-ssh-hotfix` passed with 2 stages, 3 tests, 0 warnings, 0 errors.
- Artifact inspection: `Artifact validation passed`.

## User impact

Mikey can run native-Termux Devtool validations from an SSH session without unsetting `SSH_CONNECTION`, `SSH_CLIENT`, or `SSH_TTY`.
