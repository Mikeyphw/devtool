# OCI ARM64 multiarch APT hotfix

## Result

PASS. This hotfix fixes the second live `devtool oci update tablet --apply` failure after the Ubuntu base-image correction.

## Root cause

The ARM64 OCI Dockerfile enabled Ubuntu `amd64` packages with `dpkg --add-architecture amd64`, but it kept the default ARM64 Ubuntu Ports source for every architecture. On an ARM64 Ubuntu rootfs, `ports.ubuntu.com/ubuntu-ports` publishes ARM64 indexes, not `binary-amd64` indexes. After enabling `amd64`, `apt-get update` attempted to fetch paths such as:

```text
http://ports.ubuntu.com/ubuntu-ports/dists/noble/.../binary-amd64/Packages
```

Those indexes do not exist, so the build failed at Dockerfile step 7.

## Fix

The Dockerfile now writes explicit deb822 APT sources before `apt-get update`:

- ARM64 packages use `http://ports.ubuntu.com/ubuntu-ports` with `Architectures: arm64`.
- AMD64 runtime packages use `http://archive.ubuntu.com/ubuntu` and `http://security.ubuntu.com/ubuntu` with `Architectures: amd64`.
- The existing pinned `DEVTOOL_APT_SNAPSHOT`, Android SDK, build-tools, NDK, Node, and ARM64 Android build-tool digests remain unchanged.

This preserves the intended ARM64 image behavior while still allowing the qemu-wrapped x86_64 Google NDK host binaries to resolve their amd64 runtime libraries.

## Validation

- Focused OCI image regression: 27 passed.
- Full OCI-01 through OCI-10 contract regression: 126 passed.
- Artifact validation selection is embedded in `.devtool-artifact/validate.sh`.
