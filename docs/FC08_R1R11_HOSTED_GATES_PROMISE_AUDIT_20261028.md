# FC08 R1R11 — hosted gate repair and promise audit

Source bound: `a007a7c38d26cc919574eb59e95e889492f957ac`; tree `8f9d8aaaaec1c5f84f8addbf33331df789268515`. Personal project; no release authority granted.

## Direct observations
- FC08 run 40711125377: DAG/Linux/macOS PASS, Windows FAIL at .NET target resolution. The summary failure is expected fail-closed because a lane fails; its generic DAG message is not evidence of a DAG failure.
- G1 run 40711131824: 23 shards PASS; shard 18 failed RM13 native GCC symbol inspection; job duration near the previous 10s timeout. The upstream combined summary correctly fails closed.

## Source promises and local checks
1. Explicit tracked Windows solution before actual Devtool build: `tools/fc08_host_workloads.py`; regression exercises generated command and refuses absent/untracked/other path.
2. RM13 symbol inspection timeout moderately increased from 10s to 30s, remains bounded; timeout, missing tool and failed tool are different errors; symbol/ABI checks remain enforced.
3. Test regression is deterministic and does not demand Windows/.NET or nm installed on the local authoring host. The real hosted workload is **not** replaced with a simulated check.
4. Updated cumulative ledger; source-guarded one-commit Devtool transaction; rollback on failure.

## Open external obligations
- Fresh FC08 Windows real-host lane and complete summary.
- Fresh all-green G1 24-shard full regression and summary.
- Native Termux apply and consumer/Android device qualification, BKX R3 Go transaction and FC09 closure are separate; release **NOT QUALIFIED**.

## R1R12 replacement — native test discovery correction

- R1R11 **FAILED NATIVE TEST COLLECTION** before test bodies on native Termux because an installed Hermes Agent regular `tools` package shadowed this repository's namespace `tools/`. R1R11 must **not** be counted as accepted.
- R1R12 retains the Windows `--solution FC08.sln` and 30s bounded RM13 symbol inspection source bytes verbatim. It modifies only the new test import boundary and documentation/ledger, and adds an exact-source test. No `sys.path` mutation, ambient `tools` package dependency, test skip or changed production behavior.
- **Current gates:** this is a candidate replacement for clean `main` at the original R1R10 commit; native Termux acceptance **PENDING**; Windows hosted workload **PENDING**; G1 24-shard acceptance **PENDING**; BKX R3/FC09/Android consumers **OPEN**; release **NOT QUALIFIED**.
