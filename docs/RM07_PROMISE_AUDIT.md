# RM-07 promise audit / truthfulness boundary

| Roadmap promise | Implementation state | Evidence or remaining obligation |
| --- | --- | --- |
| Linux reviewed profile | Implemented | Immutable profile and default regression |
| macOS reviewed profile | Implemented (local compilation only) | Static x86_64 profile, independent host admission; live runner not tested |
| Windows reviewed host record | Implemented (doctor only) | Hosted export refused until native bridge exists |
| Termux/Android local host record | Implemented (doctor only) | Hosted export refused; native Termux qualification not claimed |
| Pinned setup actions | Implemented | Pinned SHA checkout/Python/Go/cache, no tags |
| Requested toolchain version validation | Partial | Explicit Go 1.x version and fixed Python 3.12; broader language/SDK matrix deferred |
| Bounded matrix fanout | Implemented | Max two reviewed cells, 48 total job instances; per-cell needs, transfers and refusals |
| Trusted lock cache key | Implemented for Go compiler only | Tracked regular go.mod/go.sum, SHA, no symlinks or unreviewed replace |
| Secret exclusion | Implemented for selected cache scope | Fixed Go compiler cache path; no arbitrary paths/module-download/credential caches |
| Python dependencies and cache | Deferred (RM-08) | Cache disabled, no trust promotion |
| Local toolchain doctor | Implemented | Probes local executable versions with installed/missing/unknown, never installs |
| Hosted runner availability/hosted execution | Not qualified | Requires live GitHub receipt, not inferred from profile label |
| Affected-target pruning (RM-06) | Not implemented | Full native DAG fallback; ownership DAG deferred RM-20 |
| External release/device/signing authority | Not qualified | Explicit negative output fields and continuing gates |

**Status:** RM-07 scoped source implementation, **PARTIAL**, and RM-06 still PARTIAL. Do not mark either roadmap package fully closed without their outstanding functionality/evidence.
