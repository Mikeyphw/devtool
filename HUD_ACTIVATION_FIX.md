# Desktop HUD activation fix

The HUD could appear to do nothing for two independent reasons:

1. The Control Center ignored the result of `QProcess::startDetached()`, so a missing or unstartable HUD executable produced no visible error.
2. The HUD singleton client exited as soon as it connected to the local socket. It did not wait for proof that the existing process had actually surfaced its window. A live but hidden/off-workspace HUD therefore swallowed every launch request.

This patch:

- requires a `shown` acknowledgement from the existing HUD process;
- remaps, restores, raises, and activates the HUD on every activation request;
- uses a normal top-level `Qt.Window` rather than an unparented `Qt.Tool` window;
- searches `PATH` when the HUD is not beside the Control Center binary;
- reports missing-binary and process-start failures;
- adds `--reset-position` for one-shot position recovery;
- adds regression coverage for silent activation failures.
