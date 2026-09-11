# AOPS03 Android Signing, Instrumentation, and Device Operations Report

## Scope

AOPS03 completes AO05–AO10 on top of AOPS02. The implementation makes Android signing, instrumentation, connected-test policy, device lifecycle operations, and smoke gates consumers of the canonical Android capability/device fabric instead of new parallel Android implementations.

## AO05 — signing identity manager

`devtool.android.signing.AndroidSigningManager` owns keystore creation/import, JKS/PKCS12 migration, alias enumeration, certificate inspection/export, SHA-256 identity and expiry metadata, APK/AAB/JAR verification, and signer comparison. CLI surfaces are `devtool android signing create|list|inspect|verify|compare|import|export-certificate`.

Password values are not persisted in TOML. Supported sources are process environment, FD/stdin, prompt, and external secret command/provider inputs. Paths, aliases, expected fingerprints, mode, policy, and password *source names* may be persisted.

## AO06–AO07 — task-scoped policy and Gradle injection

Signing requirements are derived from requested Gradle tasks. Debug/lint/compile/unit-test tasks do not require release credentials merely because a project can produce a release. `mode=devtool` generates an ephemeral Gradle init script containing only keystore/alias/store-type metadata and internal environment-variable names; it contains no secret values. Project/external signing modes remain available.

AOPS03 additionally closes a cross-cutting secret boundary: planned `CommandSpec.env`, validation-session serialization, reports, and argv do not contain signing password values. Release credentials are materialized only immediately before native child-process spawn. Shell-wrapped execution modes that cannot yet carry credentials without argv exposure fail closed.

## AO08–AO09 — instrumentation and explicit device-test policy

`AndroidInstrumentationOrchestrator` uses `AndroidDeviceOperations`/`AndroidDeviceManager` for device selection and uses the staged Devtool Android builder for app/test APK assembly. It supports class, method, annotation, excluded-annotation, size, and runner arguments; captures structured results and optional logcat/screenshot evidence; and cleans up according to policy.

Both instrumentation and connected-test gating normalize `disabled`, `when-device-present`, and `required`. Optional mode records a skip when no usable device exists. Required mode fails for missing/broken transport and reports unauthorized devices as actionable failures rather than silently disabling tests.

The historical `devtool.android_instrumentation` entry point is retained as a compatibility facade; production Devtool runner plans invoke its canonical AOPS03 path.

## AO10 — device lifecycle and smoke gates

`AndroidDeviceOperations` provides install/replace/downgrade, uninstall, launch, force-stop, restart, clear-data, permission grant/revoke, shell, push/pull, logcat, screenshot, package inspection, and reusable lifecycle primitives. Smoke gates include install, upgrade, cold-start, restart, restore-restart, process-death, and signed-artifact verification.

## Security invariants

1. Password values are never written into project TOML or generated Gradle init scripts.
2. Password values are absent from planned command argv/env and durable validation session JSON.
3. Native execution resolves configured secret sources only at the final process-spawn boundary.
4. Unsafe shell-wrapped backends fail closed rather than serializing credentials into argv.
5. External secret-provider command strings are redacted from diagnostic/report surfaces.
6. Release signing is task-scoped and cannot make ordinary debug/test/lint configuration depend on release secrets.

## Compatibility

Existing AOPS02 ADB/provider/device selection remains canonical. Existing instrumentation helpers and command entry points remain available for compatibility, while normal Devtool orchestration uses the canonical builder/device path. Existing projects without explicit instrumentation policy preserve historical optional behavior.

## Validation

AOPS03 has dedicated contract tests covering signing policy, secret transport, keytool-backed identity creation/inspection/export, Gradle injection, instrumentation convergence, connected-test policy, device operations, smoke gates, configuration, CLI/completion/manpage, and validation-session redaction. Full repository regression and the strict final seal are required before the overlay is packaged.
