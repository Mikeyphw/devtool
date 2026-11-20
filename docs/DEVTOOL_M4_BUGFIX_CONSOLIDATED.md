# M4 GitHub App + Guardian + CI24 durability bugfix consolidation

## Source and delivery

Source: `Mikeyphw/devtool`, `main` at `e72869e5258030a78fac6e1551654bfdc758d7a4`, tree `ce27966adc1cf4f0e349e8a626ecb2debba34adb` (2026-11-20). Built through a checksummed immutable Python zipapp, supplied only to the verified, exact-tree native Devtool launcher v3.2.1+; **do not apply manual patches or reapply a committed session**. A changed origin/main or changed source blob is a refusal; re-audit and produce a new *distinct immutable package* instead of silently repinning this one. One named feature branch, no force/rebase/reset/auto-merge, no push or PR without explicit approval.

## M4 (GitHub App)

GitHub App installation authentication is **explicitly selected** with `--auth app` and never silently overrides PAT/gh credential priority. Verify installation identity, scoped repository, owner, permissions and expiry before requesting Git operations; secrets are absent from logs, args, persisted receipts and environment where native helper transport is available. Distinguish head and base repository installations, forbid unsupported origin rewrites, and retain the native push/PR/CI session engine and authorization gates. Full App installation acceptance requires a real authorized installation; source-only unit tests are not live App authentication qualification.

## Guardian (DTB-012)

The actual artifact validator emits `artifact-validate-script`. The committed native history recovery scope must check that exact completed, unskipped operation. The corresponding **manifest policy remains `validation`**, never synthetic `validate`. Do not broaden accepted operation aliases; forbid artificial/failed/skipped child outcomes and unverified mutation inventories. Original Guardian commit `3159bb513fd7e1cbe0055c44a608624fd33dd6d6`, history `c9ad337cc8b864750f517927f4683cb0`, and immutable artifact stay untouched. After source fix merges and native runtime updates, use recovery on the original session, not overlay reapplication.

## Lock-only XDG storage authority (DTB-014)

A solitary regular, empty, private `registry.lock` under XDG artifact-registry is a synchronization sentinel, **not a competing data authority**. A populated legacy registry remains authoritative before explicit migration. Real files, nonzero lock content, additional files, directories, symlinks and unsafe modes still fail closed. Native explicit migration acquires repository writer lock and the nonblocking registry lock; preserves the sentinel in a distinct `.layout-markers/artifact-registry-lock-only-<fingerprint>` directory without deleting bytes; then uses existing staged copying, fingerprint verification and atomic marker to establish XDG. If lock is held, destination changes or an earlier preservation exists, refuse. Interrupted recovery leaves legacy and the preserved XDG sentinel intact, never silently merging unrelated registry records.

## Interrupted summary ZIP registration (DTB-015)

The GitHub workflow/run/attempt/source, artifact ID, SHA-256, and nonexpiry are obtained from authenticated remote metadata. When a checksum-named archive already exists within the canonical download directory and has no registry record for that exact artifact, verify full local SHA-256, safe ZIP CRC/bounds and embedded debug evidence **before** appending a normal immutable registry event using native `register_paths`. Never overwrite mismatched bytes or automatically trust a local file. An already-registered archive remains a refusal for `actions download`; consumers use independent `actions verify`. A clean download uses portable exclusive-create (`O_CREAT|O_EXCL|O_WRONLY`) publication instead of `os.link` (unavailable in Termux Python 3.14) or racing `os.replace` overwrite. The final inode is never replaced; the content is fsynced and its SHA-256, inode, link count, permissions and size are checked before registry insertion. A process interruption may leave a partial unqualified file: a subsequent request rejects its mismatched SHA and requires explicit forensic recovery rather than overwriting it. Native CI24 still independently validates the full 24-shard/summary/25 artifact identity and GitHub's final source-state observation. This is **not** release/device acceptance.

## Regression coverage and scope

- New `tests/test_m4_storage_artifact_recovery.py` covers idle/held/symlink/nonzero XDG locks, explicit migration, exact GitHub source/digest ZIP reuse, no redownload/no duplicate registry event, wrong bytes and expired metadata.
- The existing `.github/workflows/devtool-launcher-v3.yml` source-scoped PR CI is extended to run the new targeted storage/artifact and GitHub App tests, with an explicit hosted cryptography dependency. Existing `tests/test_storage_layout.py`, `tests/test_storage_layout_integration.py`, `tests/test_actions_04_artifact_retrieval.py`, `tests/test_actions_g01_artifact_attempt_safety.py` remain active; no exception handling or security gates are bypassed.
- Existing `tests/test_artifact_validation_fail_closed_hotfix.py` and `tests/test_validation_manifest_skip_contract.py` already exercise required executable `validation.tests`; unresolved historical DTB-006 is not asserted to be fixed without a new current-source reproducer.
- Historical DTB-017 hosted workflow policy is not weakened; current authenticated hosted CI and exact head checks remain mandatory.
- Delivery regression, CLI schema, App provider and Guardian recovery tests run in the native validation plan. Separate 24-shard hosted regression remains mandatory before merge; the **V9 handoff** invokes the native CI24 dispatch exactly once after successful required PR CI, and then performs bounded read-only observation. The repository source itself does not auto-dispatch simply because it is imported.

## Acceptance evidence

Local handoff tests only prove package composition, checksum, AST and isolated fixture semantics. The target-side required tests run inside the native launcher before commit and must be inspected from its real stdout/stderr. The resulting commit, durable native history/session, actual draft PR head, required hosted checks, 24-shard evidence, live App installation, actual Guardian recovery and any release/device validation must be reported separately. Any unperformed stage is **not passed**.


## V9 approved one-command remote delivery (handoff, not source daemon)

Running the checksummed `native_apply_termux.sh` with its default `M4_AUTO_REMOTE=1`
explicitly authorizes the following *in this Termux foreground execution*, in order:

1. Native launcher builds/checks one immutable exact-pinned overlay, validates
   the targeted repository tests, atomically commits and adopts the native
   session; a preflight or native failure stops without remote mutation.
2. The existing native launcher `deliver --approve-remote` (or, only when
   `M4_DELIVERY_AUTH=app` is intentionally selected, the native M4
   `delivery session push/pr --auth app --yes`) performs fail-closed fast-forward
   push and draft PR creation, each with durable intent and independent
   source verification. No force push, credential fallback or automatic merge.
3. Native required PR CI is tracked at exact PR HEAD until a verified PASS.
   A CI failure, missing trigger, ambiguous run or provider outage stops **before**
   CI24 dispatch.
4. Only after required CI success is `qualification_workflows.ci24_start`
   invoked once. Its durable dispatch receipt is persisted before any POST;
   uncertain POST outcomes block retries. The handoff observes CI24 using the
   native `ci24_resume` API for up to 12 polls at 25-second intervals by default.
5. If CI24 finishes, the handoff requests live B3 qualification of the original
   session, exact attempt, 24/24 shard jobs, summary, 25 artifacts and SHA.
   A pending result is explicitly reported as pending, not as acceptance.

Set `M4_AUTO_REMOTE=0` for local-only application, `M4_DELIVERY_AUTH=app`
for a configured authorized App with a verified exact HTTPS Git origin,
`M4_CI24_WATCH_POLLS=0..60` and `M4_CI24_WATCH_INTERVAL=5..120`
for bounded on-device observation. Any error **after** local commit requires
inspecting/reusing the original durable session; **never rerun apply**.
For an already dispatched CI24 run, use the supplied read-only
`native_ci24_resume_termux.sh` with the exact session and request IDs.

Automation does not authorize release signing, device validation or merging.
Remote actions are performed on the *user's Termux invocation*, never in
background by this handoff or merely by extracting its ZIP.

## V8 CLI registry/parser parity (DTB-028)

`devtool/core_cli_schema.json` is an executable parser authority and a stable-identity registry in one source file. Adding a `delivery session push --auth` **option identity** without the corresponding nested `parser.subparsers` action causes import-time `core_registry._assert_schema_identity_binding` refusal, blocking all `devtool.cli` imports and pytest collection. `canonical_schema()` must add the parser action at exactly `delivery > session > push` and add the `app` choice to existing `pr` and `ci` actions as well as their option metadata. The V9 handoff runs the pinned runtime's actual parser/registry parity and `--auth app` parse before starting a native transaction; native delivery remains the sole authority for writes, Git push and PRs.

## V9 Android/Termux artifact publication correction (DTB-031)

V8 native source-bound validation ran 516 tests: 513 passed and 3 failed with
`AttributeError: module 'os' has no attribute 'link'` at the new
`devtool/actions_artifacts.py` publication step. Native Devtool reported
rollback succeeded. V9 eliminates `os.link` from the source transform while
preserving exclusive no-clobber publication, digest verification, registry
provenance and explicit refusal on incomplete/crashed files. The V9 plan adds
Termux no-hardlink, preexisting-file, symlink, digest-mismatch and source
preservation tests; the hosted source-scoped workflow already runs this test
module. This is source portability, not permission to suppress tests, soften
artifact validation or replay a committed session.
