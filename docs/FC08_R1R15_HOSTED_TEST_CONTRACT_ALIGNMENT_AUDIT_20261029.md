# FC08 HOST-R1R15 — host-summary legacy regression alignment

Source preimage: `70650d4433e4cd0446b04c024c7bf3fa17bbeb6d` (`main`), tree `4f6d1944cf02701c18c1e31fd3caae55ef624e68`.

## Trigger and scope

The R1R14 source correctly changed the summary's failed-host diagnostic from the misleading generic `GITHUB_DAG` to `FAILED_OR_MISSING_GITHUB_HOST_JOB:<lane>`. The legacy `tests/test_fc08_host_workload_contract.py::Contract::test_parent_dag_failures_rejected_even_with_good_artifacts` was not aligned and fails with 48 PASS / 1 FAIL in the four-file focused suite on the exact R1R14 snapshot. This is an expected-message regression, **not** a successful-host receipt or a failure in hosted Windows runtime.

## Promise-by-promise disposition

| Promise | Implementation/evidence | Status |
| --- | --- | --- |
| Legacy DAG failure rejects with exact actual lane | Updated legacy expectation to `FAILED_OR_MISSING_GITHUB_HOST_JOB:dag` | Source-delivered; targeted test required |
| Windows failure does not masquerade as DAG failure | Same legacy test asserts an independent `windows`-failed case reports `FAILED_OR_MISSING_GITHUB_HOST_JOB:windows` | Source-delivered; targeted test required |
| Do not relax four-lane, identity, checksum or exit requirements | No modifications to `tools/fc08_host_summary.py`, hosted workflow, per-lane workload or receipt schema | Preserved |
| Windows Unicode CLI fix remains included | R1R14's Windows-only `PYTHONIOENCODING=utf-8`/`PYTHONUTF8=1` is not altered | Source-delivered; new Windows CI still OPEN |
| Native application is source-bound, hash-bound, atomic and validated | Overlay pins HEAD/tree, verifies clean source and preimage hashes; native installer must validate | Prepared; native evidence OPEN |
| Hosted four-lane and release qualification | Must dispatch fresh FC08 and full G1 on the *new* exact source; independent review, BKX R3 and FC09 still separate | OPEN, not promoted |

No generated success receipt, ignored compile error, altered workflow completion policy, host toolchain downgrade, or product runtime change is permitted by this hotfix.

## Local validation

- Four-file focused regression selection: **49 PASS** after assertion correction (baseline **48 PASS / 1 FAIL**).
- All `tests/test_fc08_*.py` suites: **77 PASS** when the extracted archive's ownership mismatch is addressed through per-command `GIT_CONFIG_*` Git safe-directory configuration. An initial 3 failures without this sandbox setting were solely `fatal: detected dubious ownership`, not product regressions. No global Git config was changed.
- These are local Linux source tests; they do not replace real Windows runner evidence or 24-shard G1 acceptance.
