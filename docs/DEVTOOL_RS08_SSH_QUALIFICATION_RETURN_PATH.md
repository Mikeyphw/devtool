# RS08 — SSH qualification evidence return path resolution

Source baseline: `0165432df075ec01c720012b8bdcfe21f0c630ce`; original `devtool/ssh_qualification.py` Git blob `9e04f18c85b957d0a2b80b87af90e22152c35832`.

**Observed live error (2026-10-16):** Devtool `ssh qualify mom --no-bootstrap` completed a managed source sync and detached tmux probe, then failed with rsync code 23. Rsync with `--protect-args` treated `~/.cache/devtool/ssh-runs/qualification/q-44a0053b74a6` as a literal source directory and searched for `/data/data/com.termux/files/home/~/.cache/...`. The qualification `finally` block attempts cleanup, so the original ephemeral probe evidence may already be gone. Do not claim the full qualification passed.

**Fix:** At the qualification evidence-return boundary, resolve `~` and `$HOME` prefixes using the already-probed absolute remote `worker.home`, reject relative, traversal, missing-home or ambiguous `~user` operands, and pass an absolute path into the existing rsync return function. Keep tmux probe semantics, cleanup, source synchronization, and established SSH authority unchanged. No `rsync` downgrade, no profile/host change.

**Checks:** Unit regressions for valid `~`, `$HOME`, absolute paths, unsafe inputs, fixture transfer/nonce preservation, and refusal before rsync when remote HOME is missing. Existing SSHW06 tests should remain green. Source-bound patch installer validates local HEAD/tree and original module's Git blob before any edits. The remote real-device qualification remains pending after the code change and user-controlled rerun.

**Recovery before rerun:** use `devtool -r "$PWD" ssh workspace mom --format json` and `devtool -r "$PWD" ssh sync mom --dry-run --format json` to read managed workspace ownership and prospective deletion counts. Do not rerun live qualification or clean remote paths until these results are reviewed. `ssh qualify` currently does not produce a durable partial-failure receipt for each successful stage; improvement item **RS08-LIFECYCLE-002** should add source sync/tmux/return progress receipts, recovery identifiers, and interruption-safe cleanup.

**Release boundaries:** `ENV-A06`, `ANDROID-A16`, `DIST-A01`, `DIST-A12`, `DIST-A13`, `TEST-A11` remain NOT_QUALIFIED. The patch changes tracked source and must be committed/requalified on a new SHA before formal release qualification; neither previous CI24 nor native G3R evidence qualifies that new SHA.
