# devtool 0.5.4

- Updates Devtool Control Center to 0.3.4.
- Removes the invalid hard Debian dependency on an APT package named `devtool`; the CLI remains independently installable through pip or pipx.
- Discovers the CLI through PATH and common per-user Python executable locations, including `~/.local/bin/devtool`.
- Exposes CLI availability in Settings and avoids starting the IPC broker when no executable is available.
