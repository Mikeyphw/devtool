# Devtool consolidated bug hotfix — source-bound checkpoint

**Exact base:** `Mikeyphw/devtool` `main` commit `05e120d185ce8e81287b591bac6a221054abe94e`, tree `501f23e6fe0ed753090282e6bfc4ea7158c5c5d1`.
**Target feature branch:** `feature/devtool-ledger-consolidated-hotfix-20261124` in a separate clean worktree. Do **not** reset, fast-forward or rebase any existing PR/branch/session to prepare this worktree.

This first consolidated patch covers **DT-20261124-28**, **DT-20261124-29**, **DT-20261124-33** and **DT-20261124-34** in *actual production modules*, plus source regression tests. It is not a claim that all historical ledger bugs have been fixed or verified. The per-ID triage matrix included in the same operator records which remain and why. Existing bug fixes on current main or exact PRs must not be replayed. Native validation, commit, PR delivery and CI24 are **not performed** by this generated patch unless the user explicitly approves one native lifecycle run with separately verified worktrees/credentials.

## Behavior

- **DT-33:** pytest counts only from anchored runner summary lines; standard Python unittest `Ran N tests` + `OK/FAILED` counts are accumulated; contract descriptions with `UI-05 failed ...` no longer make phantom failures; partial/malformed runner evidence stays unknown.
- **DT-34:** schema-3 `--static-validate` accepts the manifest-derived `--skip-*` built-in phase switches but still rejects skips without any validation mode and mutually-exclusive validation modes; source-only Python validator remains a single native script action, no local Gradle.
- **DT-28:** M6 Rich CI panel probes live M7 App identity/read health only once using existing M4 read-only token logic, with a visible UNVERIFIED fallback. It cannot claim an effective write from permission metadata.
- **DT-29:** M6 Rich CI events consume the existing S6 source-bound session event stream and clearly label it historical, without adding another journal, provider GET or event authority.

## Safety gates

Exact source SHA/tree, feature branch, clean original worktree, registered remote, all ten Git blob preimages and every unchanged existing file path are mandatory. The native ZIP is deterministic; it carries `source_sha256`, `expected_post_sha256`, sorted touched paths, explicit Python-only validation tests and exact source identity. It does not run local Gradle. Historical QG01/DT19 and GA-01/02 source receipts are unchanged.

## Deferred after source triage

The ledger's remaining open or partially evidenced bugs (notably DT-19/20/21/23 Git push uncertainty, DT-08 process integration, DT-12/14/15 M3/M6, and historical G1/GA negatives) require their own current-source, multi-module native reconciliation rather than wholesale copying pinned old-session recovery code into current `main`. See the machine-readable ledger triage report bundled with the operator. All remain **NOT ACCEPTED** until native and hosted gates pass.

## Operator-native lifecycle and interruption recovery (v0.6.25)

`devop-devtool hotfix ledger` displays the disposition of all 34 ledger bug IDs without contacting GitHub, changing Git or using clipboard. `hotfix inspect --repo WORKTREE` and `hotfix check --repo WORKTREE --runtime-repo TRUSTED_RUNTIME` are read-only. `hotfix run --repo "$HOME/Code/devtool" --prepare --yes --deliver --ci24` may create **one new isolated feature worktree** only after independently verifying the current remote `main` still matches the exact pinned base SHA. It then delegates one atomic source apply/validate/commit, authorized push/PR update, native required CI, and separately authorized CI24 to the existing Devtool runtime. It never upgrades, resets, rebases, force-pushes or cleans an existing branch/worktree.

If this run is interrupted **after** the native commit, `hotfix resume --repo EXISTING_HOTFIX_WORKTREE` is a read-only no-replay operation: it reconstructs the exact original native ZIP from the committed Git parent, validates all changed paths and postimages, and attests retained native history, archive and existing session. Optional `--ci24` on `resume` calls **CI24 resume only**, never dispatch/start. No resume operation implicitly repeats a pending/ambiguous push or PR creation. Use the returned exact session identity for any separately authorized native remote reconciliation. Do **not** rerun `hotfix run` on a committed worktree.

## v0.6.27 generic native M3/M4/M5 continuity

- DT-21 production source: distinct second push intent only after explicit approval, acknowledged prior Git process termination, two matching read-only remote observations, original single-parent source and immutable first unresolved journal. A completed original negative reconciliation is **not a successful push**; it qualifies source resumability only when one successor intent is independently confirmed. Native PR/CI can then proceed without inventing a new session.
- DT-19/20 production extension: `delivery_github.push` retains short-lived read-only preflight, scoped workflow writing only when necessary, source-bound Git guard before journal writes, fixed-code sanitized subprocess diagnostics. No raw stderr, tokens or private keys are journaled.
- DT-23 production fail-closed guard: native M5 App delivery checks controller workflow write/transport support **before invoking** push. A stale runtime cannot create a new push intent; users must use a separately verified current controller. It does not silently rewire controller trust or rewrite a historical session.
- DT-08 source-specific milestone gate: every 34-ID bug disposition is included in the native payload, with tests validating source baseline, status/CI truth and complete inventory. This is not a global Library-sync gate.
- DT-03/DT-12 existing source guards: regression tests assert disabled phases remain skipped and precommit validation failures are not called postcommit adoption errors.
- Acceptance remains pending: native commit/PR/required CI/CI24, actual App workflow writes, disposition review and device/release. Never run Gradle locally.

## v0.6.28 operator recipe correction (OP-20261124-02)

The v0.6.27 ZIPAPP failed at `HOTFIX_SOURCE_ANCHOR_DRIFT:m7-keys:0` on the exact pinned `main`; the native validator was not invoked. The builder had a one-space indentation mismatch with the real M6 source, followed by a deterministic Markdown-as-Python compilation defect. The operator now matches the exact observed source windows, validates Python/JSON/Markdown according to file type, checks all ten real preimage recipes before creating any new worktree, and uses the same checked transform for initial build and postcommit recovery. Existing worktrees are reused only after clean exact-source verification. The bundle self-test exercises both M6 windows and a non-Python ledger payload. This is an operator-only repair; the 34 historical Devtool IDs and the previous eight proposed source corrections remain candidates, not accepted fixes. Historical DT-21/23 incidents are operator-owned; generic native guards are separately proposed safety enhancements. The new source archive has not been natively applied or remotely delivered.
