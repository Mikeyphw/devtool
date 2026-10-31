# R1R29-H1 — Android Python detection and actionable native blocker receipts

## Source and observed incident

Exact target is `Mikeyphw/devtool` main `7f2f97bcf4fe90f813dbc098ee500d34691cd797`, tree `cee1eb55074a4a4c1b68505f4a3cc5a5f08cbc3e` (post-R1R29 Wave B/D update, direct child of R1R29). This overlay must refuse any other HEAD/tree, dirty worktree, content mismatch, or failed validation. No remote dispatch or release action is added.

The user's real native Termux environment reported `sys.executable=/data/data/com.termux/files/usr/bin/python3`, `sys.prefix=/data/data/com.termux/files/usr`, architecture `aarch64`, git/clang/cmake/ctest/ninja present, **Meson missing**, clean Git worktree, standalone native error `NATIVE_TERMUX_ARM64_REQUIRED`, and standalone read-only storage probe `BLOCKED_FC08_R1R29_STORAGE_PROBE`. No `.fc08-r1r29-owned-*.probe` residual canary was found in `/sdcard/Download` at that moment. The earlier combined runner returned a generic error and did not preserve diagnostic evidence.

### Cause supported by independent reference

Python 3.13+ Android's `platform.system()` may return `'Android'` while `os.uname().sysname` remains `'Linux'` (Python platform documentation, and termux/termux-packages issue #29028). The R1R28 native runner and R1R29 storage probe explicitly refused any name other than `'Linux'`. These two independent failures are consistent with that mismatch. Exact live `platform.system()` output was **not** supplied, so this is a source-derived diagnosis, not a claimed on-device measurement.

## Changes and safe scope

1. Accept `platform.system() in {'Android','Linux'}` only with `os.uname().sysname == 'Linux'`, `platform.machine() in {'aarch64','arm64'}`, expected Termux `sys.prefix`, and existing prefix directory. Never promote Ubuntu/x86 or a generic Android process into Termux native acceptance.
2. The combined native CLI performs the real prerequisite preflight in its main entrypoint, preserving the prior `accept()` invocation contract for existing stub integrations and the full native runner's own checks. Missing Meson is reported explicitly as the allowlisted `NATIVE_TOOL_UNAVAILABLE_MESON`.
3. A failed native check now durably writes a bounded allowlisted `BLOCKED_UNQUALIFIED` JSON receipt containing phase and category. A failed standalone storage probe writes a similarly redacted receipt with `artifact_deleted=false`. Unknown exceptions, paths, tokens, arbitrary subprocess output and failure messages are never written verbatim. All signing/device/consumer/FC08/release gates remain false.
4. Storage behavior remains read-only unless the user again explicitly opts into the existing owned `O_EXCL` canary. No canary, downloaded overlay, or external file was touched by offline testing. The real shared-storage deletion root cause remains OPEN.
5. Add tests for Android/Linux positive labels, wrong OS/ABI/prefix/kernel, missing Meson, existing success API, safe unknown-error redaction, read-only storage failure, and failure receipt persistence.

## Promise-by-promise disposition

| Promise | Evidence | Status |
|---|---|---|
| Correct Termux Android OS label without host spoofing | Source tests for both Android and Linux labels and negative host combinations | Offline PASS; real phone pending |
| Missing Meson is actionable | RuntimeError category and CLI fail-closed receipt tests | Offline PASS; install and retest on phone pending |
| Preserve `accept()` API | Existing R1R29 regression with fake native runner | Offline PASS |
| Never echo private paths or tokens | Category allowlist and redaction tests | Offline PASS |
| Failure JSON persisted | CLI tests with read-only tmp output | Offline PASS; phone pending |
| No invalid artifact deletion or root operations | No changes to deletion implementation; canary opt-in unchanged | Source preserved; native storage pending |
| Source-pinned exact-base installer | SHA/tree, preflight, per-file hashes, validation, rollback | Package prepared; actual native Devtool apply pending |
| Hosted FC08/G1 on new commit | Fresh same-SHA run must be executed after push | OPEN |
| Native ARM64 build/test, actual shared-storage canary | Must be executed on phone with Meson | OPEN |
| Genuine HTTP 5xx/timeout, Android signing/installation, consumers/BKX R3/FC09 release | Separate gates | OPEN |

## Operator actions

Install missing prerequisite first: `pkg install meson` (or use the Termux supported Meson package source). Apply exact-source H1 overlay with `--keep-artifact`. Run the read-only storage probe and native runner before considering another canary. Inspect their JSON receipts; never infer native acceptance from offline tests or hosted Ubuntu.


## R2 corrective replacement — actual Termux installer failure (2026-10-31)

The originally distributed H1 overlay reached validation in transaction `22b36e61843d44bd9e2fab8b8595fe56` and failed **one test of 81**: `test_consolidated_native_acceptance_refuses_non_termux_before_canary`. It assumed tests run outside Termux. On native Termux the environment is correctly recognized as ARM64; validation's temporary overlay worktree is intentionally dirty, so `NATIVE_SOURCE_WORKTREE_DIRTY` was observed instead of the expected host rejection. This is a false test assumption, **not a production native acceptance failure**.

Replacement H1-R2 includes the original H1 implementation in full, changes only the negative test's host setup to explicitly mock `platform.machine() == 'x86_64'`, and records this evidence in the cumulative ledger. The negative test now verifies the intended refusal on both Linux CI and real ARM64 Android. No modification to `environment()`, native ownership/delete policy, Git cleanliness rules, token or dispatch permissions, or source/commit pin. **Apply replacement against unchanged pre-H1 base, never layer over a partial apply.**
