# FC08 HOST-R1R18 — hosted VSTest attached coverage provenance audit

**Source pin:** Commit `9cceb55b7ae9abab56f1e5aacfd57efab82fbb00`, tree `76955386d1725c38a8e7c5f923626127066a2c66`.

**Observed evidence:** FC08 GitHub Actions run [40989543287](https://github.com/Mikeyphw/devtool/actions/runs/40989543287), Windows job `123848464546`: `dotnet test Checks/Checks.csproj` exited 0 and reported one test PASS, one `fc08.trx`, and one explicit `Attachments:` path to `coverage.cobertura.xml`. However the recursive directory scan reported `trx_count=1 coverage_count=2` and refused `MISSING_TRX_OR_COVERAGE`. Linux, macOS, and DAG jobs passed; the summary correctly refused the Windows failure. The exact source/content of the second Cobertura file is not established by the failure log.

## Promise audit

| Promise | Assessment | Evidence |
| --- | --- | --- |
| Fix false rejection caused solely by a second Cobertura file | Delivered in source; hosted proof open | When multiple coverage files exist, require one unique VSTest-reported attachment path resolving to a file inside the bounded test-results directory. Do not select by filename ordering. |
| Keep test execution authenticity | Delivered locally | Exactly one parseable TRX, positive total and executed counts, every test passed, zero failures. Duplicate TRX and zero-test TRX remain refusals. |
| Keep coverage authenticity | Delivered locally | Reported coverage must be a well-formed Cobertura document with `lines-valid > 0`. Additional discovered coverage files must parse as valid Cobertura XML but do not qualify independently. |
| Reject ambiguous or untrusted file selection | Delivered locally | Reject multiple/missing reported attachments, path outside results, missing file, malformed extra XML, and a reported coverage document without source lines. Single-file receipts remain backward-compatible. |
| Preserve provenance without private paths | Delivered locally | Selected TRX, attached coverage and complete candidate-coverage hash list/count retained; no paths or raw captured output in the successful receipt. |
| Keep the real eight-stage Windows workload | Delivered in source | Only coverage selection logic changed; locked restores, Devtool build, TRX tests, pack/publish, MSVC native, negative exit remain required. |
| Real Windows host four-lane acceptance | Open | A new GitHub Actions run at post-R1R18 SHA must produce a successful Windows job and a valid four-lane `host-summary.json`. |
| Release, consumer/device qualification | Open | This patch cannot satisfy independent consumer/device, BKX R3, FC09 or release approval. |

### Remaining risk and decision boundary

The single attachment displayed in the VSTest output is independently grounded in the real process, but the provenance of the second XML is unknown. Do not delete, silently ignore, or fabricate it; parse and inventory it. If the next Windows host run rejects, inspect the precise diagnostic and do not broaden acceptance without evidence. Source-only tests are not Windows-host verification. Preserve the original git worktree and any stash, and apply transactionally only to the exact source pin above.
