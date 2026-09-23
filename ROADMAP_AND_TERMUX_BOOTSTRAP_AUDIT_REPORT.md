# Roadmap and Termux Bootstrap Audit Hotfix

## Roadmap audit

The uploaded post-OCI-10 snapshot was audited against the local OCI-10 final-seal
promise checker and the complete OCI-01 through OCI-10 contract tests. The roadmap
surface is present and coherent:

- `devtool.oci_final_seal.oci10_final_seal_report('.')`: pass, 0 failures
- OCI-01 through OCI-10 contract tests: 124 passed

No OCI roadmap payload hotfix was required.

## Termux automatic bootstrap root cause

The unrelated bootstrap audit found a real gap in destination receipt validation.
Native Termux bootstrap verified `aapt2`, `aidl`, `zipalign`, and `split-select`,
but chroot bootstrap did not. When Devtool auto-selected a Termux-hosted Android
chroot such as `androidos`, an incomplete bootstrap report could still be written
as a ready receipt. Later automatic bootstrap runs would then reuse that receipt
instead of repairing the missing Android build-tools package set.

## Fix

This hotfix makes chroot bootstrap receipts prove the Android build-tools payload
in the same way native Termux receipts already did:

- chroot provisioning reports must contain all four Android build tools:
  `aapt2`, `aidl`, `zipalign`, and `split-select`;
- chroot bootstrap validation checks those paths inside the chroot with `test -x`;
- stale chroot receipts without verified build-tool paths are rejected;
- future automatic bootstrap policy will not reuse an incomplete chroot receipt.

## Validation

The hotfix was re-audited after implementation:

- bootstrap command tests: 27 passed
- Termux/toolchain/aapt2/bootstrap regression set: 122 passed
- OCI-01 through OCI-10 contract tests: 124 passed

The overlay validation repeats the final-seal check plus the bootstrap, Termux/toolchain,
AAPT2, and chroot receipt regression tests. The broader OCI-01 through OCI-10
contract set was run during the audit loop before packaging.
