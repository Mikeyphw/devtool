# QG01-G1-F02 — Hosted G1 JSON-only output and complete failure diagnostics

Status: **SOURCE CANDIDATE ONLY**; native delivery, required CI, fresh CI24,
new hosted G1, native 13-lane G3R, and FS03 require independent evidence.

## Evidence and exact source

- Repository: `Mikeyphw/devtool`, branch `feature/devtool-r06-base-adoption`.
- Preimage commit: `ba112e79267864f24be550068dc86c41d1fa7bec`.
- Preimage tree: `627668c783993a00930770dae666923a5fe79445`.
- Historical failed hosted G1: run `45190682963`, attempt 1, `failure`.
- `candidate-consumer` succeeded; hosted `android-g1-hosted` failed.
- CI24 for that commit is distinct; do not reuse earlier source's CI24 proof.
- Hosted log: `HOSTED_ANDROID_G1_REFUSED Expecting value: line 2 column 1 (char 1)`.
- In `g3r_hosted_android.produce`, the child returned code zero, but
  `json.loads(process.stdout)` refused the non-JSON machine channel.
- Source G1's `run_gate` executes `run_specs` with human progress; stdout
  contamination is the inferred mechanism. The runner did not upload the
  child log and the exact contaminating bytes are therefore not observed.
- A second defect: the existing bounded failure writer ran only for child
  nonzero/empty output; JSON decoding and unqualified JSON yielded no
  `android_g1_failure.json`. The workflow warned the artifact was absent.

## Corrective implementation

- `devtool/android_g1_gate.py`: route execution/progress to stderr and emit
  only one canonical JSON machine report on successful exit. Do not suppress
  native validation errors or alter real Gradle execution on the hosted runner.
- `devtool/g3r_hosted_android.py`: reject all non-JSON stdout; do not strip
  diagnostics or scan for an embedded JSON suffix. Emit source/run/attempt
  bound, public-safe failure diagnostics on JSON decode and report-validation
  failure, without exposing raw stderr, credentials or secrets.
- `tests/test_qg01_g1_f02_machine_output.py`: exercise human-channel and
  machine-channel segregation, invalid JSON and malformed qualified-report
  refusal, sanitized failure artifact and positive structural path. All fake
  Gradle subprocess outputs are mocked; no local Gradle or APK installation.

## Strict acceptance

Only the actual Devtool-native check/apply/commit/session, remote PR update,
required CI, fresh 24-shard regression, fresh hosted Android G1 run with exact
artifact/attempt/digest, native 13-lane G3R and FS03 can establish acceptance.
This document must not be treated as any of those receipts. Historical failed
runs remain immutable and cannot be retried under an unchanged source.
No force push, destructive reset, local Gradle, merge or release is authorized.
