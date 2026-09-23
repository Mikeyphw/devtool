# Devtool OCI/PRoot operational guide

This guide documents the operator-facing behavior of the `ssh-proot-oci` provider.
It is a visibility layer over the existing SSH, EXO, and PRoot-Distro lifecycle;
it does not introduce a second executor or a second source of truth.

## Image update policy

Use `devtool oci status PROFILE` to inspect the canonical Android image and
container without mutating the worker. Use `devtool oci update PROFILE` to review
the immutable update plan. The worker is changed only when `--apply` is present.
The update path is build, install/replace from verified archive, then validate
against the definition digest, build-context digest, archive digest, and install
receipt.

## Source-sync, cache, and worker reuse

Managed SSH source synchronization remains the authority for repository source.
OCI execution receives `/workspace` as a bind and never treats persistent caches
as source. Approved Gradle/Android caches are worker-local binds under the OCI
cache root and are excluded from source synchronization. A named managed
`ssh-proot-oci` run prepares the worker once and reuses that preparation for all
external command phases in the same run.

## Diagnostics and HUD fields

`devtool oci diagnostics PROFILE` emits a redacted, non-mutating summary for CLI,
JSON, and HUD consumers. The payload groups evidence into cards:

- PRoot-Distro runtime readiness
- canonical image/container state
- provider and phase-node identity
- image provenance
- source/cache policy
- worker reuse policy
- managed workspace identity

The same visibility summary is attached to OCI phase metadata as compact
`oci09_*` fields so terminal HUDs and Android HUD bridges can display OCI state
without parsing large phase records.

## Resilience and artifact return

Resume, cancel, recover, and returned artifact manifests target the canonical
OCI phase node produced by the SSH/tmux owner. Returned artifacts include a
producer identity digest so the operator can connect an artifact to its run,
profile, source sync, image, container, and build-context evidence.

## Secret handling

Registry credentials such as `PD_DOCKER_AUTH` are transported only through
transient mode-0600 state during the OCI operation. They are redacted before
metadata, diagnostics, HUD summaries, return manifests, and logs are made
durable. Diagnostic payloads fail closed if known secret markers are detected.

## Final seal and compatibility

`devtool oci final-seal` (`devtool oci seal`) runs the local OCI-10 roadmap
seal. It does not connect to the worker or mutate remote state. The seal checks
that OCI-01 through OCI-09 modules, tests, operator documentation, redacted
diagnostics, and the top-level CLI help golden remain coherent after the OCI
command family is introduced. Use `--format json` for CI or artifact finalizers
and `--strict` when a failing final-seal check should exit non-zero.
