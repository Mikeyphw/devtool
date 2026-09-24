# FRX-09 Remote Execution Repository Identity Report

## Scope

FRX-09 carries federation repository identity and parent/child lineage across
existing execution providers without introducing a second remote workspace or
path-mapping system.

## Delivered

- Provider-neutral federation transport metadata contract.
- Local-root ownership and effective-repository lineage validation.
- Termux-host/chroot mapping through the existing chroot path map.
- PRoot mapping through existing bind/repository mount mappings.
- SSH and SSH+OCI mapping through provider-resolved workspace roots.
- Whole-run SSH/tmux forwarding of `DEVTOOL_FEDERATION_*` metadata.
- Nested execution provenance containing child identity, mapped roots, lineage,
  provider, and artifact return ownership.
- SSH/OCI return producer evidence records the actual return-bundle root.
- Fail-closed behavior for missing/stale/mismatched remote identity mappings.

## Validation policy

FRX-09 is not a validation gate and is applied with `--no-validate`. The next
required validation gate remains FRX-12; FRX-10 and FRX-11 continue the
post-Gate-2 implementation sequence.

## Local audit

- Focused FRX-09 transport suite: 8 passed.
- FRX-01..09 plus Termux chroot, execution environment, SSH/tmux, managed SSH
  workspace/return, OCI repository, and OCI resilience slice: 234 passed.
- Touched modules compile successfully.
