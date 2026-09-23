# OCI ARM64 live APT multiarch hotfix

## Status
PASS

## Root cause
The ARM64 OCI image was still enabling `APT::Snapshot` while the base image came from the live `ubuntu:24.04` ARM64 rootfs. That allowed the base ARM64 libc and the requested `libc6:amd64` runtime libraries to come from different package-version timelines, causing APT to report broken held packages during `devtool oci update tablet --apply`.

## Fix
- Keep `ubuntu:24.04` as the resolvable ARM64 base image.
- Bootstrap `ca-certificates` before explicit multiarch source configuration.
- Keep ARM64 packages on `ports.ubuntu.com/ubuntu-ports`.
- Keep amd64 runtime libraries on the primary Ubuntu amd64 archives.
- Disable `APT::Snapshot` for this multiarch image so ARM64 and amd64 libc packages remain version-aligned.
- Preserve reproducibility through the image context digest and pinned Node, Android command-line tools, Android API/build-tools/NDK, and ARM64 build-tool asset digests.

## Validation
- Focused OCI ARM64 live-APT regression: 29 passed.
- Full OCI-01 through OCI-10 contract regression plus live-APT hotfix test: 128 passed.
- Finalize hook is read-only and does not mutate `devtool.egg-info`.
