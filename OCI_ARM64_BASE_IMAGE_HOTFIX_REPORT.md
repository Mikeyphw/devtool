# OCI ARM64 base image hotfix v1

## Result

PASS — this overlay fixes the ARM64 OCI image bootstrap failure reported as:

```text
Image not found: 'ubuntu:noble-20260911@sha256:c5ddb18c368db61a2f5b0971380f0ff1a1bb50e1da176cc48c9e5c2dbf3afb5b'
```

## Root cause

The OCI-03 image contract and Dockerfile pinned the base image to a dated Ubuntu tag plus digest:

```text
ubuntu:noble-20260911@sha256:c5ddb18c368db61a2f5b0971380f0ff1a1bb50e1da176cc48c9e5c2dbf3afb5b
```

The registry does not provide that dated tag, so `devtool oci update tablet --apply` failed before any Devtool Android/ARM64 toolchain provisioning could start.

## Fix

The Dockerfile now starts from the resolvable multi-arch Ubuntu LTS tag:

```text
ubuntu:24.04
```

The Android/ARM64 toolchain contract remains pinned where it matters for Devtool reproducibility:

- APT snapshot remains `20260911T235959Z`.
- Node archive digest remains pinned.
- Android command-line tools archive digest remains pinned.
- Android API/build-tools/NDK versions remain pinned.
- ARM64 replacements for `aapt2`, `aidl`, `zipalign`, and `split-select` remain SHA-verified.
- Embedded Devtool runtime digests remain checked by the existing OCI image contract.

The rejected bad base reference is recorded in `image-manifest.json` so the regression cannot silently return.

## Validation

- Focused base-image regression: `27 passed`
- Full OCI-01 → OCI-10 contract set: `126 passed`
- Disposable artifact apply lifecycle: passed with `0 warnings, 0 errors`

