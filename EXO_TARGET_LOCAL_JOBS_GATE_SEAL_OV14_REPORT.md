# EXO TLJ-14 — Target-Local Jobs Gate + Seal

## Purpose

TLJ-14 corrects the campaign gating/sealing gap. TLJ-12 was named as a final seal, but it was distributed as a `--no-validate` overlay, so it did not function as a real Devtool validation gate/seal overlay.

This overlay is the authoritative target-local jobs gate/seal overlay for the TLJ campaign.

## Apply mode

Do not pass `--no-validate`.

Required apply command:

```bash
cd "$HOME/Code/devtool"

devtool -r "$PWD" --target devtool \
  apply-overlay /sdcard/Download/devtool_exo_target_local_jobs_gate_seal_ov14_v1.zip
```

## Manifest contract

- validation.required=true
- validation.rollback_on_failure=true
- validation script: `.devtool-artifact/validate.sh`
- finalize script: `.devtool-artifact/finalize.sh`
- commit enabled after successful gate

## Gate coverage

The embedded validation gate runs the TLJ public-surface regression suite, including:

- schema and config loading
- discovery/preflight
- runtime provenance
- execution affinity
- workflow boundaries
- completion and doctor
- jobs inspection
- plan export
- preflight export
- run summary export
- UI/machine summary
- final-seal/adoption contracts
- TLJ-14 gate/seal assertions

## Verdict

PASS in local replay. This overlay should now be used as the real gate/seal overlay for the target-local jobs roadmap.
