# devtool 0.5.0

- Adds a per-user Unix-domain socket broker for Devtool Control Center.
- Streams run snapshots and incremental progress updates over newline-delimited JSON.
- Supports multiple producer runs and multiple subscribed Control Center clients.
- Adds reconnect snapshots and state-file recovery fallback.
- Routes cancellation requests to the exact devtool run and terminates its active child process group.
- Adds `devtool ipc-server` and `DEVTOOL_IPC_SOCKET`.
- Updates Devtool Control Center to 0.3.0 with native IPC status, reconnect controls, and per-run Cancel actions.
