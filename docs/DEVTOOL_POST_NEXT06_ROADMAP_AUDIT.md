# Devtool reconstructed NEXT-00–06 roadmap audit and gate register

**Scope:** built Git tree `536b90c02049b45f651fbe1bd2a4df03b361b782` at reconstructed commit `cef18fa68828ad683483b7bc117626bd3fd85ac8`, following NEXT-01, 02, 03, 04, 05 and 06 source-bound installation commits. This is a **source audit**, not proof that the independent phone checkout has the same Git tree.

The original `docs/DEVTOOL_NEXT_CAPABILITIES_ROADMAP.md` is a multi-phase plan through NEXT-11. **NEXT-00 through NEXT-06 are not equivalent to completion of the entire roadmap.** A phase with bounded working source can remain partial against its full acceptance gate.

| Phase | Source/gate disposition | What is still required |
|---|---|---|
| NEXT-00 | `SOURCE_IMPLEMENTED_LOCAL_TESTED` | Device installed-wheel CLI, tracked documentation copy-paste on Termux |
| NEXT-01 | `SOURCE_IMPLEMENTED_LOCAL_TESTED` | Actual historical MMRL receipt and native recovery acceptance |
| NEXT-02 | `SOURCE_PARTIAL` | Authenticated cross-owner joins, trusted EXO producers, complete eligibility for every CI owner |
| NEXT-03 | `SOURCE_PARTIAL` | Real host jobs and multi-host/per-node matrices, changed-target/caching policy |
| NEXT-04 | `SOURCE_PARTIAL_HOTFIXED` | Go test/vet/race receipts; Python installed-wheel smoke, coverage and Ruff; real hosted and consumer provenance |
| NEXT-05 | `SOURCE_PARTIAL` | Actual six-repo adoption, generalized legacy config/wizard/rebase |
| NEXT-06 | `SOURCE_PARTIAL` | Real XBS extraction; dependency graph discovery/automatic pruning; independent consumer/CI/native validation |
| NEXT-07 | `NOT_IMPLEMENTED` | .NET solution dependency matrix, TRX and Linux/Windows acceptance |
| NEXT-08 | `NOT_IMPLEMENTED` | Rust, JS/TS, non-Android JVM, Shell and C/C++ providers and real-language acceptance |
| NEXT-09 | `NOT_IMPLEMENTED` | Android convention plugin discovery, multiline settings and custom variants |
| NEXT-10 | `NOT_IMPLEMENTED` | Typed Actions wrappers, resume/diagnose/capabilities and Termux operator UX |
| NEXT-11 | `EXTERNAL_GATES_OPEN` | Current-source native, hosted, consumer, device, formal security and independent release authority |

## Reproduced NEXT-04 source gaps repaired by this hotfix

* **DT-057:** Go binary ELF/PE/Mach-O checks and module buildinfo identity were present, but the verifier trusted the manifest `CGO_ENABLED` and ignored the binary buildinfo `CGO_ENABLED`, `GOOS` and `GOARCH`. A synthetic CGO-enabled Go executable could pass as a CGO-disabled distribution despite a matching ELF header and Go module. This candidate now enforces these buildinfo settings; no signature/authenticity promotion.

* **DT-058:** Wheel verification parsed `WHEEL` tags but merely required one tag; the wheel filename and `WHEEL` tags could disagree, and a console-script name could point to a different function than the tracked `pyproject.toml`. The hotfix validates bounded PEP 427 filename tags and exact declared script targets against wheel metadata and the existing verified RECORD file.

## Explicitly NOT delivered by the source overlays

Generalized NEXT-03 multi-host/per-node GitHub workflow dependency export; substantive provider test/vet/race/coverage evidence; automatic NEXT-05 adoption wizard and unrestricted legacy migration; automatic NEXT-06 cross-repository dependent-graph pruning; and NEXT-07/.NET, NEXT-08 remaining languages, NEXT-09 Android custom variants, NEXT-10 operator experiences. Each remains open **as a source feature or independent acceptance**, not treated as a regression in a previously stated bounded lane.

## Authority closure sequence

1. **Hotfix source:** apply only to exact post-NEXT-06 tree, inspect scoped commit and installed-wheel behavior; run focused/negative tests and full source tests. This qualifies only source-level checks, not R09 external evidence.
2. **Native/hosted:** collect authoritative current-source CI24/G3R/FS03/A09 with exact run/attempt and independently verified artifacts, and native Termux installed-wheel evidence.
3. **Consumer:** verify real Skybind, BKX, chroot-ctl, Cinderbox and configured downstream consumers, plus real XBS migration if planned; obtain exact independent source SHA and operational receipts.
4. **Device/security:** independently supply Android instrumentation, provenance, root broker and SSH worker gates, and R08 N01/N02/C04/C05/C06/Q01/Q02/U01 plus ENV-A06, ANDROID-A16, DIST-A01/A12/A13, TEST-A11.
5. **Release:** only an independent formal-release owner can set `formal_release_qualified=true`. The R09 inventory reports remain false until then.

## Suggested command for read-only current-source inventory

```sh
python3 -m devtool.rmp_r09_campaign --repo "$HOME/Code/devtool" --format json
```

For source tests, run the campaign with `--run-tests` only when an appropriate test environment is available. Check exact outcomes; do not interpret `PASS_R09_INVENTORY_TESTS_NOT_RUN_EXTERNAL_OPEN` as source validation.

## Evidence recorded

1. Reconstructed git log and exact tree at `cef18fa`. 2. Previous NEXT-01 through NEXT-06 delivery archives and local disposal tests. 3. This hotfix’s independent verification runs and rejection tests. 4. R09 read-only inventory showing `PENDING_INDEPENDENT_AUTHORITY` for hosted, consumer, device, formal security and release. **No Android, GitHub, formal security or remote release authority was invoked in this source audit.**
