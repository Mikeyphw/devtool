# ENV-X05 — Gradle takeover broker / real launcher authority

Campaign position: **34/70**  
Gate window: **1/3** (`ENV-X05 -> EXEC-X05 -> GRADLE-TAKEOVER-G1`)  
Owning scope: environment/provider/launcher/policy truth. Universal execution ingress remains EXEC-X05.

## Delivered boundary

ENV-X05 adds one optional Gradle takeover authority without introducing a second Gradle planner, scheduler, cache, run store, or receipt authority.

- `[gradle.takeover]` is parsed through the existing Gradle runner-option boundary. Takeover is disabled by default.
- Modes are typed as `off`, `observe`, `manage`, and `strict`; scope is `devtool`, `shell`, or `both`.
- Origins and intents are typed. `WORK` may enter canonical planning; `PROBE` and `CONTROL` intentionally bypass work planning while retaining real-launcher authority.
- `devtool.gradle_takeover.broker_gradle_invocation()` reuses `GradleInvocationRequest` and `plan_gradle_invocation()` from the existing ENV-X03 planner.
- One real-launcher resolver owns wrapper/system selection. The resolver strips Devtool takeover shim directories before system PATH lookup, rejects explicit resolution back into the shim, and destination-qualifies provider identity.
- Project-wrapper identity retains wrapper realpath, executable digest, wrapper properties digest, wrapper JAR digest, distribution URL/version, and existing wrapper-distribution SHA identity.
- Chroot, PRoot, SSH, external-device and federated destinations are deferred rather than reusing host PATHs; native Termux and ordinary host execution may resolve locally.
- Protected `DEVTOOL_GRADLE_TAKEOVER_*` environment names cannot be supplied by repository/variant environment configuration. An internal capability file is user-state owned and used only for recursion/bypass execution.
- Shell takeover installs a generated `gradle` shim plus a POSIX/bash/zsh-compatible adapter. Coverage deliberately claims bare `gradle` only; it does **not** claim direct `./gradlew` or arbitrary nested-script Gradle interception.
- Shell assets are shared safely across repositories: disabling one repository does not remove assets still required by another enabled repository.
- `devtool gradle takeover status|enable|disable|doctor|explain` and `devtool gradle real -- ...` are implemented. A hidden shell-intercept entrypoint backs the generated shim.
- Configuration and receipt metadata are identity-significant but exclude internal capability material.

## Local qualification

Canonical wrapper-equivalent workflow (with local host execution override because the repository config targets native Termux):

```text
python -m devtool -r . --execution-environment host invoke roadmap-env-x05
Stages:   3 passed, 0 failed
Tests:    95 passed, 0 failed, 0 skipped
Warnings: 0
Errors:   0
```

Breakdown:

- `tests/test_env_x05_gradle_takeover.py`: **16 passed**
- retained ENV-X03/ENV-X04/config regression slice: **79 passed**
- `python -m compileall -q devtool tests/test_env_x05_gradle_takeover.py`: PASS

A broader `tests/test_cli.py` sweep remains **130 passed / 8 baseline failures**. Replaying those eight nodes on the untouched post-LIFE-G1 baseline produces the same eight failures, so they are not introduced by ENV-X05. They are carried as pre-existing TEST/LIFE-era test-drift defects rather than being absorbed into Gradle takeover.

## Deliberate non-ownership

ENV-X05 does **not** yet rewrite every Devtool-owned Gradle command vector. That belongs to EXEC-X05 (35/70), which must route wrapper/invoke/workflow/TestSpec/validation/artifact/Android/SSH/federation Gradle ingress through this authority before reusable execution identity is finalized.

GRADLE-TAKEOVER-G1 (36/70) remains separate and must prove real-process interception, recursion safety, convergence, strict opaque-shell policy, evidence, cancellation, and release qualification.
