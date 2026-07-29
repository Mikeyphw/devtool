# Phase 37: Bridge Roundtrip Probe

Phase 37 adds a safe one-tap bridge probe that verifies the real Android → Termux → shell wrapper → authenticated BridgeReceiver → Termux pending-result path.

## Probe path

1. Android starts a Termux RUN_COMMAND operation with `bridge_operation=roundtrip-probe`.
2. The Phase 36 wrapper emits shell-side launch sentinels.
3. The probe command reads the Termux marker files from `$HOME/.config/devtool`.
4. The command verifies the marker token, package, and component against the wrapper environment.
5. The command sends an authenticated callback with `BRIDGE_ROUNDTRIP_CALLBACK_RECEIVED`.
6. Termux returns the pending result with a stdout marker.
7. Android classifies the probe as passed only when all required checkpoints are present.

## UI

Settings → Diagnostics now includes **Bridge roundtrip probe**. The tile reports passed, failed, running, unavailable, or permission-needed state and stores the latest run id, exit code, checkpoints, and diagnosis for support bundles.

## Scope

This phase does not add Repo Manager Doctor or executor/path repair. It focuses on bridge liveness and exact pre-run communication failure boundaries.
