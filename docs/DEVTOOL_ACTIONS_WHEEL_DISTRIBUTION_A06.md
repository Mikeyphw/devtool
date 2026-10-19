# ACTIONS-A06 — Installed-wheel CLI schema distribution repair

## Incident and boundary

Cinderbox ACTIONS-A05 GitHub run `39296491876` failed on Ubuntu after `pip install` successfully built and installed Devtool commit `08eb13991b5d1c623b64fdd8f9524418ee27207e`. The first CLI invocation failed because `devtool/core_cli_schema.json` was absent from the installed wheel, even though it was present in the pinned Git source checkout. That run uploaded deliberately incomplete evidence and was not qualified.

A05-R1 temporarily executed Devtool from the exact pinned source checkout with virtual-environment dependencies and source-import identity checks. A05-R2 verified successful Cinderbox run `39301086093` (source `7675f3471326b3ca52a9474a17d96a6bde2943d6`) and its read-only native DAG evidence, without certifying Android build/device/release lanes.

## Repair

`[tool.setuptools.package-data]` now explicitly includes `core_cli_schema.json` alongside existing Devtool resources. The CLI's canonical schema authority remains unchanged, and no runtime fallback or implicit source-checkout resolution is introduced. The regression suite independently checks the TOML contract, parser/schema identity, an actual offline wheel build, the ZIP entry and byte-for-byte resource hash, plus a cold `python -S` import from the installed isolated wheel.

The wheel build uses the host's local setuptools backend with `--no-index --no-build-isolation --no-deps`, copies source to a temporary directory and leaves the Git tree unmodified. A failed build or import is a failure, never a silent skip.

## Continuation

1. Apply and accept this producer overlay on `Mikeyphw/devtool` directly after accepted ACTIONS-A04 commit `08eb13991b5d1c623b64fdd8f9524418ee27207e`.
2. Push the accepted producer commit and run one external GitHub wheel distribution smoke test before declaring remote distribution qualified.
3. Only then update the consumer runtime pin in a separate, source-scoped Cinderbox A06 Android-build adoption. A05's verified read-only source DAG remains unchanged until intentionally migrated.
4. Reuse existing Cinderbox target-local `debug-apk`, `verify-debug-apk`, and `connected-tests` jobs and the current debug-only/signing policy; do not claim device or release qualification based on Ubuntu runner checks.

**Qualification boundary:** A06 local acceptance proves the local built-wheel contract. It does not retroactively qualify a previously failed GitHub run, and it does not prove hosted wheel installation, Android SDK/device readiness, or formal release obligations.
