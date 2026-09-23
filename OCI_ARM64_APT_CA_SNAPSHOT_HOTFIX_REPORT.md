# OCI ARM64 APT CA/Snapshot Hotfix Report

## Result
PASS — the ARM64 Android OCI image definition now bootstraps CA certificates before enabling Ubuntu snapshot HTTPS and keeps the amd64 runtime-library source policy required for qemu-wrapped Google NDK host tools.

## Failure reproduced from tablet
`devtool oci update tablet --apply` reached the image builder but failed during Dockerfile step 7 because:

- `APT::Snapshot` redirected Ubuntu indexes to `https://snapshot.ubuntu.com/...` before `ca-certificates` was installed.
- The minimal Ubuntu base therefore could not validate the snapshot certificate.
- `apt-get update` ignored the failed indexes, so the following package install could not locate `build-essential`, `openjdk-*`, `python3`, `qemu-user-static`, or the required amd64 runtime libraries.

## Fix
The Dockerfile now performs a two-stage APT bootstrap:

1. Use the base image's default HTTP ARM64 Ubuntu ports sources only long enough to install `ca-certificates`.
2. Clear APT lists, enable `amd64`, install architecture-scoped ARM64 and AMD64 sources, then enable the pinned `DEVTOOL_APT_SNAPSHOT` and install the full image package set.

This preserves:

- `ubuntu:24.04` as the resolvable multi-arch base.
- `DEVTOOL_APT_SNAPSHOT=20260911T235959Z` for the actual toolchain package install.
- ARM64 packages from `ports.ubuntu.com/ubuntu-ports`.
- AMD64 runtime libraries from `archive.ubuntu.com` / `security.ubuntu.com`, needed by qemu-wrapped x86_64 NDK host binaries.
- Android API 36, build-tools 36.0.0, NDK 29.0.14206865, Node 22.20.0, and ARM64 replacements for `aapt2`, `aidl`, `zipalign`, and `split-select`.

## Validation
- `tests/test_oci_arm64_apt_ca_bootstrap_hotfix.py`: passed.
- `tests/test_oci03_android_image_contract.py`: passed.
- Full OCI-01 through OCI-10 contract selection plus the new hotfix test: 128 passed.
- Disposable artifact lifecycle: passed after packaging.
