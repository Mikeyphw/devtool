# FC08 R1R29 — Native Termux / owned-storage canary / independent G1 JUnit recount

## Base authority and merge decision

- This overlay is for **only** `Mikeyphw/devtool` R1R28-H1 commit `0935fc77c5c6b4ea86e3a3a5b1b0501854f18a51`, tree `1c5ff34a79a42465596a1cb2cb0c73ca3d962637`. Any source/tree drift or dirty worktree blocks application. Previous R1R28/H1 and R1R25–R1R27 evidence remains historical, not new-commit qualification.
- The adjacent scopes reviewed were (A) actual native ARM64 execution/owned-temp lifecycle, (B) diagnosing the shared `/sdcard/Download` source-cleanup failure without touching user files, and (C) closing independent G1 JUnit recount from authenticated GitHub artifact ZIPs. They can be implemented in one merge window, but their acceptance proofs remain separate.
- R1R28-H1 hosted evidence: FC08 [41153526696](https://github.com/Mikeyphw/devtool/actions/runs/41153526696) all five jobs PASS; G1 [41153533268](https://github.com/Mikeyphw/devtool/actions/runs/41153533268) 24 shards, 609 modules, 7,005 tests, 0 errors/failures/skips; manually authorized matrix [41157961422](https://github.com/Mikeyphw/devtool/actions/runs/41157961422) PASS with three separate child JSON receipts, and the intended failure probe. This is **R1R28-H1 evidence only**. No R1R29 hosted execution or Termux execution has yet occurred.

## Changes and reviewed boundaries

1. `tools/fc08_r1r28_termux.py` additionally requires a clean Git worktree before/after the original real CMake/CTest+Meson native runner, verifies ARM64 ELF source and performs a real owned-file **identity-drift refusal followed by exactly-authorized cleanup** inside its temporary fixture. Its existing `transaction_rollback_integration_qualified=false` remains false; this is not a full transactional installer acceptance on an Android device.
2. `tools/fc08_r1r29_storage_probe.py` offers read-only `--artifact` metadata inspection, recording only a hashed filename and metadata fingerprint, never source bytes or raw path. Optional `--probe-dir ... --yes-create-owned-canary` creates a unique `O_EXCL|O_NOFOLLOW` file on the chosen filesystem, checks open-file vs path identity and parent identity, and only unlinks that **new, owned** file on exact match. If identity cannot be verified, it leaves the canary for manual review rather than force-deleting it. It never unlinks an existing ZIP or promises to repair the real source cleanup bug. Default is read-only, and its CLI requires **actual native Termux ARM64**, not hosted Ubuntu or chroot.
3. `tools/fc08_r1r29_native_accept.py` is a single orchestration command that runs real native acceptance and optional shared-storage inspection/canary with source-bound, atomic JSON receipt. No signing, Android installation, root commands, external consumer operations, background tasks, or release promotion.
4. `tools/fc08_r1r29_g1_recount.py` reads the 24 original G1 shard ZIPs **and** original summary ZIP against the prior `fc08_r1r28_evidence.py` 30-ZIP authenticated GitHub API inventory. It checks exact source SHA, one G1 run ID, all shards and module uniqueness, inventory digest, primary testcase counts, no failures/errors/skips, each original JUnit/log digest, anti-symlink/zip-slip/size limits, raw ZIP SHA-256 and comparison to the final summary. It counts primary `<testcase>` nodes rather than JUnit suite headers that can contain pytest subtest extras. Emits `g1_junit_independently_recounted=true` only after complete success; keeps all release/device/Windows-signing flags false.
5. The original GitHub collector accepts **optional** `--recount-g1`. Default archive-integrity-only behavior and existing workflow permissions remain unchanged. Paths to source scripts are resolved by explicit file location to avoid the Termux Hermes Agent `tools` import collision.
6. `tests/test_fc08_r1r29_termux_storage_g1_recount_20261031.py` includes full 24-shard synthetic offline recap, malicious ZIP/XML, wrong digest/summary, original-path redaction, no-touch canary, negative host, Git source/file-bound contracts and explicit false release gates. All tests are offline and send no GitHub POST.

## Promise-by-promise acceptance

| Promise | Evidence at creation | New-source / native status |
|---|---|---|
| Source bound transactional overlay (SHA/tree/original files), no hidden deletion | Exact-base manifest, preflight, staged post-hashes, rollback-on-failure validation | ZIP build/isolated install should be independently verified; phone apply **OPEN** |
| No mutation of user ZIP under read-only default | Source contract and local tempfile tests | **PASS locally**, actual shared storage **OPEN** |
| Optional canary only deletes its uniquely created owned file | O_EXCL + parent fd and snapshot checks, local negative tests | **PASS locally**, Android FUSE/external storage **OPEN** |
| Native ELF AArch64 build/test plus replacement-file refusal | Existing native runner strengthened, offline contract; Linux host must refuse | Source **PASS**, actual Termux execution **OPEN** |
| Confirm real user's original failed `/sdcard/Download` deletion cause and remediate | Probe is deliberately non-destructive; no policy relaxed | **OPEN** — canary ≠ real artifact cleanup |
| Independent complete G1 archive/JUnit/log/summary recount | Full synthetic 24 ZIPs PASS and **all 24 original H1 shard ZIPs independently recounted (609 modules, 7,005 primary cases), with original JUnit/log SHA-256 and G1 summary matched** | **PASS for H1 original evidence**; new R1R29 source **OPEN** |
| Original 30-archive GitHub digest/source binding | Actual 30 R1R28-H1 original ZIPs retrieved; all archive bytes SHA-256 matched GitHub API digest; immutable per-artifact inventory retained | **PASS for H1 only**; new-source capture **OPEN** |
| New R1R29 hosted FC08 + 24-shard G1 | Needs new SHA and new GitHub Actions runs | **OPEN** |
| Full real Android rollback, signer, installed instrumentation, BKX R3/Telegram/Studyflow adoption | Not modified, no forged evidence | **OPEN** |
| Genuine 5xx/timeout, raw Windows signed authority, FC09 seal and formal release | Not claimed | **OPEN** |

## Operator procedure and evidence separation

1. Apply overlay against exact R1R28-H1 `0935fc77` with clean worktree and `--keep-artifact` on Termux. Push resulting commit and run the **same-SHA** FC08 host workflow and full G1 24-shard workflow. Never copy old green results to the new SHA.
2. On phone, run `python3 tools/fc08_r1r29_native_accept.py --repo "$PWD" --out "${TMPDIR:-$PREFIX/tmp}/fc08-r1r29-native.json"`. Optional shared storage read-only inspection uses `--artifact /sdcard/Download/<overlay.zip>`. Only if explicitly desired add `--probe-dir /sdcard/Download --yes-create-owned-canary`; the user's ZIP remains untouched. Inspect any residual canary if a drift refusal occurs; do not force-delete or automatically retry.
3. For hosted artifact review run `python3 tools/fc08_r1r28_evidence.py --root "$PWD" --host-run ID --g1-run ID --archive-dir DIR --out REVIEW.json --fetch-archives --recount-g1`. Or run standalone G1 recount using the downloaded provider-backed 30-ZIP inventory `python3 tools/fc08_r1r29_g1_recount.py --provider-review REVIEW.json --archives DIR --out G1-RECOUNT.json`.
4. Do not set `fc08_overall_qualified` or `release_qualified` to true. Native source tests are not Android sign/installed tests; G1 is not a release seal; a canary success is not a fix to Android shared storage.

## Remaining operational debt

- Actual Termux `/sdcard/Download` cleanup failure after applied commit: no observed root cause or verified fix. Keep using `--keep-artifact`.
- Python 3.14 `urllib.error.HTTPError` fixture `ResourceWarning` hygiene: not modified by R1R29.
- Genuine GitHub 5xx/network timeout: unobserved; the journal still refuses blind retry.
- Windows raw TRX/Cobertura and signed binary payload authority, actual consumers and FC09 168-promise independent crosswalk: open.

## Post-build historical original-artifact replay (independent verification)

After implementing the recount, the 30 original ZIP archives for R1R28-H1 were retrieved from GitHub Actions and **all 30 ZIP SHA-256 values** matched the digest metadata returned by GitHub's artifact API. The authenticated H1 source is `0935fc77c5c6b4ea86e3a3a5b1b0501854f18a51`; host run `41153526696`, G1 run `41153533268`. The new R1R29 recount script read all 24 original G1 shard `receipt.json` files, 609 JUnit XMLs and 609 logs, checked original recorded hashes, rejected duplicate module identities, compared all primary testcase counts with the source-bound H1 G1 summary, and recovered **7,005 primary passed tests / 609 unique modules / 24 shards**. This is an actual original-byte historical replay, not merely fake 24-shard fixtures. Source-bound per-archive digests and per-shard totals are recorded in `docs/FC08_R1R29_H1_ORIGINAL_G1_JUNIT_RECOUNT_EVIDENCE_20261031.json`.

This proves that the new parser handles actual H1 pytest JUnit format and that historical H1 original evidence is consistent. It does **not** qualify the new R1R29 commit, the on-device Termux runner, any Android install, or signed Windows artifacts. Run fresh same-commit FC08+G1 and the native phone acceptance separately.
