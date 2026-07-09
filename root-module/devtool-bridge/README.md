# Devtool Root Bridge

Optional, non-Zygisk KernelSU/Magisk companion for Devtool Live HUD. It does not replace the rootless Termux `RUN_COMMAND` backend.

The app receives root through the installed root manager and calls the module's narrow `devtool-root-bridge` wrapper. The wrapper accepts authenticated `devtool`/`devtool-androidos` commands only, records logs and PID files under `/data/adb/devtool-bridge`, and supports cancellation.

Install the generated ZIP from KernelSU, KernelSU Next, Magisk, or APatch, reboot, grant the app root, then open **Settings → Optional root bridge → Pair root bridge**.
