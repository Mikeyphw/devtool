# FC08 HOST-R1R24 — Source-bound R1R23 hosted closure + native ABI refusal expansion

## Authority and independently inspected evidence

- Exact proven source: `Mikeyphw/devtool` commit `ac815e2fb293329b776671eefbc92c8e7277fa9b`, tree `4a2d1cfb202e76250a918714a5f7ece5c51a9a4e`. This is **R1R23**, not the new R1R24 code.
- GitHub [FC08 run 41119773037](https://github.com/Mikeyphw/devtool/actions/runs/41119773037) completed **5/5 jobs** on attempt 1; original five GitHub artifact ZIPs have been fetched and their SHA-256 hashes, embedded source/tree/attempt, check-set digests and four-lane summary cross-checked. The five original archive digests are in `FC08_R1R24_R1R23_HOSTED_EVIDENCE_REVIEW.json`.
- R1R23 hosted lanes: DAG **5**, Linux **14**, Windows **8**, macOS **3**; fail-closed inventory **PASS**, `fc08_overall_qualified=false`, `release_qualified=false`.
- Linux Cargo actual refusal: `cargo build --frozen` exits **101** with `CARGO_LOCKFILE_UPDATE_BLOCKED_MODERN`; `Cargo.lock` hash retained. This closes the specific R1R21–R1R23 Cargo incident at the exact R1R23 source, not every possible Cargo negative case.
- DAG live scoped API proof: actual read-only workflow GET **200** and `workflow_dispatch` denial **403**; **does not qualify** successful, failed, retried, or deduplicated dispatch lifecycles.
- GitHub [G1 run 41119781440](https://github.com/Mikeyphw/devtool/actions/runs/41119781440) completed **25/25 jobs**, 24/24 shards; downloaded summary archive confirms **6,910 tests**, 604/604 modules, zero failures/errors/skips, `formal_release_qualified=false`. Individual G1 shard ZIPs have **not** been independently re-audited in this R1R24 review; this is hosted summary and job verification, unlike the exhaustive R1R19 check.

## R1R24 promises, tests and disposition

| Promise | Behavior | State |
|---|---|---|
| Close the R1R23 hosted Cargo blocker using immutable real evidence | Store six fetched ZIP IDs and digests, verified receipt identity/check digests and G1 summary, preserving explicit false release gates | **Complete for R1R23 source** |
| Exercise more true Linux RM-13 ABI negative cases | Build real CMake C++ executable with existing Devtool runner, copy resulting real ELF into owned temporary files; require strict refusal of ELF class corruption, foreign ELF machine ID and leaf symlink | **Implemented; new-host evidence pending** |
| Never mutate the original binary or carry malformed artifacts into release | Check original SHA and exact original bytes, temporary directory automatically cleaned even after errors, preserve false release receipt | **Implemented; local tests pass** |
| Refuse false acceptance and unrelated errors | Catch `CppError` only for exact scenario-specific reason; fail if verifier accepts corrupt/foreign/symlink input, or reports wrong refusal cause | **Implemented; negative tests pass** |
| Preserve R1R23 Cargo, GitHub permission and other host behavior | Existing DAG 5, Windows 8, macOS 3; Linux grows 14→15; Cargo/uv and Windows/MSVC unchanged | **Source audit and local regression pass; R1R24 hosted pending** |
| Close old tests without hiding regressions | Update R1R22 historical count assertion from 14 to 15 while retaining every Cargo refusal assertion and adding dedicated 5-case R1R24 suite | **163/163 FC08 tests passed locally** |
| Validate full R1R24 host and G1 after application | New real FC08 attempt must pass all 5 jobs (Linux 15 checks) and new same-source full 24-shard G1 | **OPEN** |
| Qualify formal release and consumers | Separate real repeated/successful/failed dispatch, ABI/lock matrix expansion, Windows signer, native Termux ARM64, Android signer/installed identity, BKX R3 consumer rollback, FC09 and release seal | **OPEN** |

## Operational issue retained for separate repair

R1R23 native Termux `apply-overlay` [run `45fe249305c845e898195bd6c570a881`] successfully applied, validated, and committed `ac815e2`, but returned `artifact_cleanup_failure` while attempting to remove its `/sdcard/Download` ZIP. Cause of cleanup-worker refusal was not available in the supplied terminal summary; do not infer storage permission or delete by force. Use `--keep-artifact` for R1R24, retain the original ZIP and avoid blind reapply of an already-committed exact-source overlay. Five Python 3.14 `ResourceWarning` messages from `HTTPError` test fakes are separate test-hygiene work; they did not fail R1R23 validation. Record both for follow-up instead of marking them closed.

**Scope warning:** R1R24 code is a source change; the current R1R23 hosted PASS and G1 receipt cannot be promoted to the next commit. Never rewrite consumer repositories, rerun failed source cleanup blindly, or call FC08 release-qualified because hosted checks passed.
