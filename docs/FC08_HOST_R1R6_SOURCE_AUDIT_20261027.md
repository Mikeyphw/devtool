# FC08 HOST-R1R6 — source-bound remediation audit

Current base: `3ddb1c3525ddabcce13f1d86caabc18beef7c214` / `13b3cf2b338d8340153ad806bc57056a0798a963`.

| Current contract | Implementation | Evidence and disposition |
|---|---|---|
| Python/Node execution must exit 0 on real success | `devtool/runners.py`: child adapter finalized after true postprocessing | Real Linux CLI npm build+test reproduction changed exit 1 -> 0; HOSTED OPEN |
| Failed provider must remain failed | Adapter receives actual finalized returncode; no suppression | Existing fail-closed HOST summary preserved; HOSTED OPEN |
| Windows has no POSIX resource | `devtool/cli.py`: absence represented as `null` only for optional performance stats | Import-with-resource-blocked test PASS; real Windows OPEN |
| Windows locks remain real | `devtool/host_file_lock.py` + seven authority consumers use Windows byte-range locks; shared requests serialized exclusively | Unit contention tests + POSIX interprocess lock tests PASS; real Windows OPEN |
| No POSIX lock regression | On POSIX `fcntl.flock` reused unchanged | Unit tests PASS, preexisting single-run tests PASS |
| G1 source ancestry but not stale immediate parent | `tools/fc08_host_capture.py`: Git ancestry verification | Source assertion and test; live capture OPEN |
| Identity and SHA safety | Exact base/head/tree, per-file preimages and source-bound overlay validator | Native Termux OPEN until actual operator apply |
| External HOST workloads | 4 required, fail-closed summary, no release promotion | GitHub run 40517927121 FAILED; rerun required after push |
| G1 all-shard | Full Python 24-shard and Ed25519 | OPEN on new source |
| FC08 native Termux/Android/consumers | Separately executed source/device/provenance authority | OPEN |
| FC09 168 promises | 160 active plus 8 RM19 cancellations | OPEN; RM19 cancelled |

**Limitation:** Windows `msvcrt.locking` is conservative and cannot prove true POSIX-equivalent shared locking or mixed-version interoperability. It never silently grants an unlocked path. Native Windows process/lock tests and higher-level .NET/MSVC workloads must run before claiming qualification.


## R1R6 native Termux fixture correction

- Operator's R1R5 native `apply-overlay` run `f446c7284ba747aa93685af803f26f3d` revealed one missing test-environment handoff: `test_actual_node_cli_returns_zero_and_seals` inherited a sanitized Termux environment and the nested CLI refused `host-android`.
- R1R6 is a **full replacement on the same clean base** (`3ddb1c3525ddabcce13f1d86caabc18beef7c214`), not a hotfix layered on a failed transaction.
- Only the new test fixture and the permanent improvement ledger/audit changed beyond R1R5. This reuses the real-interpreter `_fixture_termux_environment()` and explicitly rejects forged interpreter paths. No production host normalization change.
- Native Termux validation and GitHub workloads stay OPEN until externally verified.


## R1R7 rebased integration on main (2026-10-27)

This is not a release or hosted qualification. The exact previous HOST-R1R6
Termux attempt c298fa2f315d4f87852938e37f558a82 failed at an actual npm
script with exit 1. The failure message omitted the npm debug-log explanation.
R1R7 adds a source-level native Termux npm script-shell selector, leaving
operator overrides authoritative; it is a hypothesis-guided remediation,
not confirmation of the original underlying failure. The targeted regression
runs a real npm build rather than skipping it. The Go output spool P1/P2
source remains unchanged except for adjacent npm lifecycle fixes.

Acceptance OPEN: native Termux full validation, Windows real toolchain and
interprocess lock checks, hosted Linux provider execution, fresh 24-shard
G1, BKX staged R3 Devtool transaction, FC08 consumer/device authority and
FC09 final 160-promise seal. RM19 remains cancelled.


## R1R7 rebased integration on main (2026-10-27)

This is not a release or hosted qualification. The exact previous HOST-R1R6
Termux attempt c298fa2f315d4f87852938e37f558a82 failed at an actual npm
script with exit 1. The failure message omitted the npm debug-log explanation.
R1R7 adds a source-level native Termux npm script-shell selector, leaving
operator overrides authoritative; it is a hypothesis-guided remediation,
not confirmation of the original underlying failure. The targeted regression
runs a real npm build rather than skipping it. The Go output spool P1/P2
source remains unchanged except for adjacent npm lifecycle fixes.

Acceptance OPEN: native Termux full validation, Windows real toolchain and
interprocess lock checks, hosted Linux provider execution, fresh 24-shard
G1, BKX staged R3 Devtool transaction, FC08 consumer/device authority and
FC09 final 160-promise seal. RM19 remains cancelled.


## R1R7 rebased integration on main (2026-10-27)

This is not a release or hosted qualification. The exact previous HOST-R1R6
Termux attempt c298fa2f315d4f87852938e37f558a82 failed at an actual npm
script with exit 1. The failure message omitted the npm debug-log explanation.
R1R7 adds a source-level native Termux npm script-shell selector, leaving
operator overrides authoritative; it is a hypothesis-guided remediation,
not confirmation of the original underlying failure. The targeted regression
runs a real npm build rather than skipping it. The Go output spool P1/P2
source remains unchanged except for adjacent npm lifecycle fixes.

Acceptance OPEN: native Termux full validation, Windows real toolchain and
interprocess lock checks, hosted Linux provider execution, fresh 24-shard
G1, BKX staged R3 Devtool transaction, FC08 consumer/device authority and
FC09 final 160-promise seal. RM19 remains cancelled.

### R1R7 capture provenance note

The capture utility binds the new canonical main branch and original squash ancestor. It never claims a pre-squash feature-branch run qualifies the current main source. No actual GitHub dispatch is claimed.


### R1R8 native npm notifier evidence and scope

- Exact R1R7 native `npm run build` failed in Devtool (`5a7c59bdd38b46ac8f45cc9282b49a73`). Both fixture packages pass npm directly but fail when passed through the isolated Devtool plan; npm's debug log supplied no internal stack.
- Operator directly reran the unchanged native unittest with `npm_config_update_notifier=false`; it PASSED in 50.477s. This is controlled workaround evidence, not source installation evidence and not proof of the npm internal mechanism.
- R1R8 adds an *opt-outable* native-Termux-only default for managed Node runner environment. Explicit npm notifier configuration takes precedence. Existing script-shell preference, build/test execution, Go spool source and hosted failure gating are unchanged.
- Still pending: native atomic apply/test on the actual new source, real hosted Linux/Windows and G1, and all external release/consumer gates.
