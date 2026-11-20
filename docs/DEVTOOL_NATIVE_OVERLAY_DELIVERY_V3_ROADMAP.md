# Devtool Native Overlay Delivery v3 — Implementation Roadmap

**Status:** M0–M2 merged into the pinned `main` baseline; M3–M9 are roadmap work, not claims of implementation or native/hosted acceptance. This proposed document change is not yet committed.  
**Target:** `Mikeyphw/devtool` — current native overlay pipeline and self-executable `.pyz` profiles.  
**Goal:** Bring the Launcher v3.2 source-bound, end-to-end delivery experience directly into native overlay application without creating a second apply engine, session journal, Git/GitHub client, or CI engine.

## Architectural baseline

The merged Devtool code already owns manifest parsing (`devtool/artifacts.py`), executable overlay v2 classification and canonical bootstrap (`devtool/executable_overlay.py`), atomic overlay transactions and commit, executable validation plans (`devtool/native_validation_plan.py`), post-commit adoption (`devtool/native_overlay_delivery.py` and `devtool/delivery_session.py`), push/PR orchestration (`devtool/delivery_github.py` and delivery-session commands), and committed required-CI policy (`devtool/delivery_ci_policy.py`). Launcher v3.2 (`devtool/launcher_contract_v3.py`) coordinates many of these existing actions and adds the self-executable source/runtime contract.

**Design decision:** Introduce an *opt-in* manifest `delivery_contract` for delivery v3. Do not change the meaning of existing `validation`, `apply`, `inventory`, `repository`, or `executable_overlay` fields. An ordinary overlay remains an ordinary overlay unless it opts into this delivery capability. Existing ZIP, v1, v2 and immutable launcher artifacts remain byte-for-byte valid under their original contracts.

## Manifest design (proposed, not implemented)

```json
{
  "schema_version": 3,
  "type": "overlay",
  "base_contract_mode": "exact-tree",
  "base_commit": "<exact target pre-apply HEAD>",
  "base_tree": "<exact target pre-apply Git tree>",
  "repository": {
    "remote": "git@github.com:owner/repository.git",
    "branch": "feature/source-bound-delivery",
    "enforce_remote": true,
    "enforce_branch": true
  },
  "inventory": {"format": 1, "entries": ["<real format-1 inventory entries>"]},
  "validation": {"required": true, "tasks": ["<real bounded native task IDs>"]},
  "apply": {
    "atomic": true,
    "requires_clean_worktree": true,
    "commit": {"enabled": true, "strategy": "single", "message": "feat: source-bound delivery"}
  },
  "delivery_contract": {
    "version": 1,
    "runtime": {
      "compatibility": {"minimum": "3.2.0", "major": 3},
      "resolution": "trusted-verified-compatible",
      "verify": ["commit", "tree", "launcher-module-blob", "cli-schema-blob"]
    },
    "session": {
      "after_commit": "adopt-or-resume",
      "bind_to": ["artifact-digest", "transaction-id", "result-commit", "branch"]
    },
    "remote": {
      "base_repository": "owner/repository",
      "base_branch": "main",
      "base_sha": "<verified upstream PR base SHA>",
      "push": "requires-explicit-approval",
      "pull_request": {
        "mode": "requires-explicit-approval",
        "draft": true,
        "title": "feat: source-bound delivery"
      }
    },
    "ci": {
      "policy_path": "config/delivery-required-ci.json",
      "observe": "on-explicit-request",
      "exact_pr_head": true,
      "qualification": "required-workflows-only"
    },
    "recovery": {
      "after_commit": "resume-only",
      "unknown_remote_outcome": "read-only-reconcile"
    }
  }
}
```

**Important:** This is a *shape sketch*, not a valid complete manifest or currently accepted CLI schema. The real inventory and corresponding source/post hashes must be fully supplied; the `validation.tasks` values must be actual registered native tasks; permitted shapes will be formalized in Milestone 1. Existing v2 fields should not be duplicated inside `delivery_contract`. Source `base_commit` (pre-apply target) and `remote.base_sha` (upstream PR target) are distinct identities.

## Milestone 0 — Source audit and contract baseline

**Outcome:** One precise map of what already works and what is genuinely missing.

- Inventory the canonical native apply, artifact parsing, executable v1/v2 profiles, transaction receipts, delivery-session adoption, required-CI and CLI schema paths.
- Reconcile tests, CI workflows, documentation and test discovery against current `main` and the currently reviewed feature PR; record the exact SHA/tree before editing.
- Classify each proposed feature as **existing**, **integration only**, or **new validation/UX**. Preserve meaningful security and regression tests; consolidate overlapping tests and remove genuinely obsolete handoff-specific assertions only with equivalent current coverage.
- Record security invariants: target worktree is a clean named branch; verified origin and source/tree/blobs; artifact checksum/inventory; no implicit base changes; no reset, rebase or force push; no automatic clipboard; no full-screen host.

**Acceptance:** Published component/contract inventory and a test matrix mapping each invariant to at least one maintained current check. No code behavior changes in this milestone unless required to correct contract documentation.

## Milestone 1 — Versioned declarative manifest contract

**Outcome:** The native verifier can parse, normalize and explain v3 delivery declarations without performing mutations.

- Add the optional `delivery_contract.version=1` extension, with strict supported keys, types, bounds, and fail-closed unknown required capabilities.
- Bind runtime requirements, exact artifact/source identity, session policy, upstream base identity, remote approval boundaries, CI policy and recovery rules.
- Reuse existing root `repository`, `base_commit`, `base_tree`, `inventory`, `validation`, `apply` and executable-profile fields; disallow conflicting duplicate declarations.
- Provide normalized read-only `inspect`/`check` projections with an actionable reason for each refusal; never treat static-only checks, skipped tests or a missing CI policy as passed execution.
- Keep v1/v2 acceptance unchanged. New v3 fields are not silently ignored by old runtimes.

**Acceptance:** Positive and adversarial parser tests (unknown fields, incompatible versions, malformed SHA, path traversal, aliasing, conflicting declarations, missing executable validation, wrong branch/origin, CI policy mismatch). Existing v1/v2 fixtures still pass unchanged.

## Milestone 2 — Verified runtime and preflight parity

**Outcome:** Native apply can select the latest compatible *trusted* Devtool runtime under the same safety properties as the v3.2 launcher.

- Resolve a verified Devtool >=3.2 runtime from explicitly trusted worktrees/cache; allow a verified detached-HEAD runtime while requiring a named target branch.
- Attest runtime commit, tree, launcher module blob and CLI schema blob before importing or invoking native execution; validate compatibility independently of user-controlled PATH or working-directory aliases.
- Preserve separate runtime and target worktrees; reject drift/dirty target state and incompatible runtime contracts before local mutation.
- For `.pyz`, require **native preverification before first execution** of any untrusted archive. Do not rely on the archive's embedded `__main__.py` to prove its own trust.
- Use real stdout/stderr, supported plain/native terminal options, no Textual/fullscreen apply host, no clipboard copying and no invented environment knobs.

**Acceptance:** Positive native check with clean exact source; negative tampered runtime/source/module/schema, detached target, dirty worktree, symlink/traversal, untrusted bootstrap, runtime drift, and wrong-origin cases fail before mutation.

## Milestone 3 — Atomic apply, commit and post-commit session continuity

**Outcome:** A single native apply may complete *local* transaction, executable validation, commit, and durable session adoption; recovery never replays an already committed overlay.

- Reuse the native transaction, validation and commit engine. Do not duplicate extraction, rollback, validation scheduling or Git mutation logic.
- After a qualified commit, invoke the existing `adopt-committed` authority automatically from the native coordinator (for ordinary ZIP and verified executable PYZ alike).
- Journal and bind artifact SHA, native history/operation/transaction IDs, validation outcomes, source HEAD/tree, result parent/tree, exact changed paths/blobs and resulting commit to a **single existing delivery session**.
- If post-commit adoption is interrupted, recover the committed receipt read-only and resume adoption; do not roll back committed work or rerun apply.
- Keep `native_apply.state=COMMITTED` distinct from the remote delivery session `stage=PLANNED`.
- Refuse ambiguous/duplicate/conflicting sessions, unresolved native transactions, failed/skipped required checks, and changed HEAD after commit.

**Acceptance:** Crash/fault injection before apply, during validation, before/after commit, during adoption, and on repeated invocation; exactly one session for one qualified commit; correct rollback for precommit failures; no extra commit or reapply after commit; ordinary ZIP and PYZ contract parity.

## Milestone 4 — Explicit remote push/PR and exact hosted CI qualification

**Outcome:** Native delivery can continue a committed session through approved push, draft PR and optional CI observation, using existing remote journals. Add a first-class, least-privilege **GitHub App installation authentication provider** that reuses the same native delivery and Actions clients, with no new push/PR/CI engines.

- Remote mutations require **separate explicit operator approval**; a manifest alone cannot authorize push, PR, force push, merge, release or Actions dispatch.
- Use existing source-bound native delivery-session push/PR operations, journaling remote intent before network writes; verify origin/fork identity, upstream base repo/ref/SHA, exact local HEAD and server-returned PR head/base.
- Default to draft PR. Never force push, rebase, reset, auto-merge, silently switch upstream, or POST twice after an uncertain response.
- On push/PR ambiguity or connection failure, use read-only GitHub reconciliation and preserve the existing session for operator approval; do not infer success.
- Load required workflows from committed `config/delivery-required-ci.json` for the correct base repository/commit (including fork workflows). Verify exact PR HEAD, event, workflow ID, run attempt, check conclusion and branch/ruleset applicability when visible.
- When the policy is absent, changed or unavailable, distinguish **NOT QUALIFIED**, **BLOCKED** and **WAITING** from **PASSED**; never infer formal qualification from arbitrary green checks. Full 24-shard repository regression remains a separately requested acceptance scope and is not run by default on Termux.
- Expose native `status`, `resume`, `deliver`, `ci`, `report` paths. After commit, use these rather than re-running `apply`.


### GitHub App authentication foundation — M4 (explicitly part of remote delivery)

**Current-source gap:** `devtool/github_actions.py` `resolve_token()` supports `auto`, `env`, `gh`, and `none`; an externally supplied installation token can currently work as a bearer credential, but Devtool does not create/refresh installation tokens or bind an App installation to its native Git push transport. `devtool/delivery_github.py` separately uses Git's configured `origin` credentials for push. These must become *one auditable credential-selection interface*, without replacing source-bound transactions or delivery intent journals.

- **First-class provider and backward compatibility.** Extend the real parser/schema only when implemented with explicit `app` authentication (and an explicitly configured automatic selection rule); retain `auto`, `env`, `gh`, `none`, user PATs and interactive SSH. Support opt-in local Termux and hosted CI credentials; do not silently change an existing user's selected identity, Git remote or permission scope. An App installation token is not a general user PAT.
- **Installation identity, JWT and renewal.** Verify configured App ID, installation ID, installation account and repository selection against authoritative GitHub API responses. Sign short-lived App JWTs with a trusted private key or appropriately secured external signer; exchange them for installation tokens restricted to the exact repository set and minimum required permissions. Installation tokens normally expire after about one hour; renew them ahead of expiry and after bounded, *read-only* auth failures, but never retry an uncertain mutation automatically. Prevent clock-skew/replay and require stable installation identity throughout a session. Never persist access tokens in the delivery journal.
- **Secure secret storage and transport.** Protect private keys with user-owned restricted storage or a secure signer, refuse world-readable/symlinked/key-substituted material, never write tokens/private keys to source trees, logs, console, JSON, artifacts, Git remote URLs, process argv, shell history, crash dumps or CI receipts. Git-over-HTTPS push must obtain its credential through a verified one-operation credential helper/AskPass or protected descriptor, **not** by rewriting `origin`, embedding a token in argv/URL or exporting it into unrelated children. Retain the current normal non-force `git push` and remote head reconciliation.
- **Permission and capability preflight.** Map each requested API/Git operation to *actual current GitHub App permissions and repository membership*, including Contents read/write for source/Git, Pull requests write for PR mutations, Actions read for monitoring and Actions write for approved dispatch, plus any additional workflow-file permission GitHub requires. Validate separate head/fork and upstream-base installations; fail before intent recording if repository scope or capability cannot be demonstrated. Never assume `/user` access proves App availability; do not infer authorization from a secret's existence.
- **Identity and approval remain independent.** Valid App credentials do not approve push, PR, workflow dispatch, merging, protection-rule changes, release or repository mutation. Keep separate explicit authorization, durable remote intents, exact head/base refs and CI evidence; preserve draft PR defaults and no force-push/auto-merge. Select App identity **per operation**, with auditable, secret-free receipt metadata (App slug/ID, installation ID, repository, requested/granted capability, token expiry class), never the credential itself.
- **Actions event behavior.** Verify App-authored pushes/dispatches against actual GitHub event and Actions registration; do not assume all workflows trigger, bypass branch protection or report CI passed solely because API authentication succeeded. Correlate workflow ID, event, run attempt, exact PR HEAD and the committed required-CI policy. No unapproved dispatch or repeated POST after unknown outcome.
- **Provider tests and deployment.** Add narrow positive and negative tests for token expiry/refresh, wrong/suspended/deleted installation, selected-repository omission, missing permissions, wrong head/base fork, 401/403/404 distinctions, HTTP redirects, secret-redaction and credential-helper injection. Keep provider tests alongside existing `github_actions`/`delivery_github` suites; consolidate overlapping checks, never remove live security regression gates just for green CI. Document operator-guided GitHub App registration/installation and CI secret/signing setup; do not require a publicly exposed Termux callback server for normal pull-based operation.

**M4 App acceptance:** Source-pinned Termux read-only capability probe, separately approved real push and draft PR using a verified installation identity, exact-HEAD Actions qualification, explicit user/SSH/PAT fallback, and hosted negative provider/secret-leak tests. A credential preflight pass is **not** a remote delivery, CI or release pass. This foundation enables later M7–M9; it does not itself implement unattended automation, repository creation or installation-consent bypass.

**Acceptance:** Real hosted PR at the exact actual HEAD, exact required CI checks with bounded polling, fork/same-repo identity tests, duplicate POST/fork hijack/drift/CI registration gap/failed, cancelled and skipped negative cases, and durable recovery after remote uncertainty. Release and device acceptance stay distinct.

## Milestone 5 — Migration, UX and launcher-optional cutover

**Outcome:** Native overlay delivery is the default *first-class* path for new packages while launcher v3.2 remains a compatible optional wrapper.

- Extend native CLI and canonical generated schema/help/completions with a minimal, documented `check → apply → status/deliver/ci` experience. Define supported names from the real parser; do not assume proposed options already exist.
- Support both ordinary `.zip` and verified `.pyz` without changing executable v1/v2 bootstrap bytes; maintain read-only inspection of older immutable artifacts.
- Implement a stable terminal-native summary: source identity, targeted tests requested/executed/skipped, native transaction and commit, session ID, PR URL and actual HEAD, CI scope, and unresolved steps. Use supported no-clipboard/no-fullscreen controls.
- Update docs, examples, test discovery, workflow path filters and CI requirements. Remove only genuinely obsolete launcher-specific duplication when equivalent native checks cover the same risk; do not remove a failing test to make CI green.
- Validate one complete source-pinned ZIP route and one preverified self-executable PYZ route through local native checks, Termux device smoke and hosted PR checks. Do not claim device/hosted results from Linux-only tests.
- Document precise compatibility and retirement criteria. Keep Launcher v3.2 usable until all native acceptance gates have passed; avoid abrupt deprecation or forced repinning.
- Surface the M4 GitHub App provider in canonical help, config, auth diagnostics and completions only after its parser/schema has shipped; preserve PAT/`gh`/SSH behavior and record which identity actually authorized a remote operation, never credential values.

**Acceptance:** Same native outcome and evidence for ZIP/PYZ, round-trip resume after interruption, no regression for legacy archives, no automatic clipboard/fullscreen behavior, and a complete auditable acceptance report.

## Milestone 6 — Termux-first vertical Rich Live console (overlay + GitHub Actions CI)

**Outcome:** One responsive, **portrait-first native Rich Live** operator surface for overlay delivery **and** the current GitHub Actions/CI monitor. This milestone starts **after M5 is accepted**, and must not expand M3–M5 implementation or acceptance scope. The existing HTML mockup is a *visual/interaction reference only*; the actual UI renders inside Termux with Python Rich, not a WebView/browser or Textual/full-screen host.

- **Unify both existing entrypoints.** Migrate the current inline GitHub CI monitor (`devtool/delivery_rich.py`, `NativeRichMonitor`, `ci_snapshot_view`, `actions_run_view`) and the existing read-only operator views (`devtool/operator_rich.py`) onto shared vertical layout components. Add overlay application to that same event-driven view. Retain the canonical native overlay, RUN/event, delivery-session, GitHub provider, CI registrar and required-CI verdict engines; the UI remains a projection and never an execution/qualification authority. Do not introduce a second CI watcher, GitHub client, event journal, polling loop or apply engine.
- **Single-column Termux portrait hierarchy.** Display, top-to-bottom: (1) compact repository/branch/exact source/runtime/artifact identity; (2) stage list with current task and bounded progress; (3) latest timestamped real native events; (4) original command output/log navigation; (5) transaction, commit, session, PR and CI evidence with explicit pending/refused states; (6) contextual keyboard hints. Use one consistent visual language in *both* overlay and GitHub Actions/CI modes, with a compact 40–60-column layout, comfortable 60–80-column layout, and wider enhancement without switching to a horizontal desktop dashboard.
- **Real-time evidence, not animation-as-proof.** Consume only source-bound events, RUN records, transaction/validation receipts, session receipts, and correlated GitHub Actions runs/attempts/jobs. Progress bars show actual determinate counters when provided; otherwise use an indeterminate activity marker clearly labeled as such. Never synthesize steps, timestamps, test totals, checked states or CI success. Preserve unmodified stdout/stderr, exit codes and durable logs; use Rich suspend/reflow or equivalent supported APIs when child output must appear without losing diagnostics. Keep a bounded event window without discarding authoritative logs.
- **Move the current GitHub CI TUI into this console.** Make the unified vertical Rich Live view the normal interactive GitHub CI observer, not merely a similar-looking second dashboard. Preserve exact PR HEAD, run attempt, workflow ID, job and step conclusions, required vs optional check classification, pending/failure/skipped states, native policy and session receipts. Preserve existing foreground keyboard behaviors (selection with `j/k` or arrows; Enter details; `v` verbose; `l` explicit logs; `[`/`]` log paging; `r` refresh; `q` detach); reconcile context-specific operator read-only keys without changing their safety/approval boundaries. Log GETs remain explicitly requested and source-bound. Retire duplicate default Textual/full-screen CI presentation **only after** equivalent interactive and accessibility coverage is verified; do not delete its security tests prematurely.
- **Credential identity and health in the shared UI.** Project only the existing M4 credential diagnostics: selected auth mode, GitHub App name/installation ID, repository coverage, required/granted capability, refresh/expiry state, and permission drift; redact keys and tokens. Display separate fork/base access and a clear distinction between `AUTHENTICATED`, `AUTHORIZED_FOR_OPERATION`, `REMOTE_VERIFIED` and `REQUIRED_CI_QUALIFIED`. Provide a guided link/instruction for required installation approval without attempting it from a read-only TUI. Do not use the UI to make credentials or an unauthorized installation appear operationally complete.
- **Nerd Font first, graceful fallback.** Offer optional Nerd Font icons for Git, Python, files, checks, actions, logs and sessions, but never rely on an installed font as a security or capability gate. Use supported configurable icon mode (Nerd Font / Unicode / ASCII) after defining its actual CLI/config interface; never invent operational flags. Apply Unicode display-width aware truncation/wrapping (including ambiguous-width, combining glyphs and emoji) so borders align in Termux. Colors and glyphs cannot be the only source of status meaning.
- **Native terminal and safety contract.** `Rich.Live` must operate inline (`screen=False`, no alternate screen, `transient=False`) on a real compatible TTY; keep native real stdout/stderr and keyboard state restoration on exit, signal, exception and terminal resize. Support `DEVTOOL_EXECUTION_PRESENTATION=plain`, `DEVTOOL_JOB_UI=plain`, `DEVTOOL_TEXTUAL_APPLY_HOST=0`, `DEVTOOL_TEXTUAL_EXECUTION_HOST=0` without disabling Rich's ordinary inline output. Respect `NO_COLOR`, `CLICOLOR=0`, `TERM=dumb`, non-TTY/CI/JSON output, small screen/SSH/tmux and reduced-refresh settings; provide faithful plain readable logs without clipboard copying, copy-on-select, invisible errors or ANSI control injection from untrusted provider content. Separate display-only interactions from operator-approved GitHub mutations and releases.
- **Consolidate, do not bypass, existing checks.** Expand current `tests/test_delivery_rich.py` and canonical operator/overlay UX suites instead of creating version-specific gate chains. Add PTY width/resize, terminal-state restoration, shell stream coexistence, Nerd Font/Unicode/ASCII width, non-TTY/JSON/no-color, failed/skipped/ambiguous CI, explicit log fetch/pagination, detach/reconnect, dirty/source-drift and user-input escape/redaction adversarial tests. Update current launcher/CI workflow path filters and run the focused suites at the actual PR HEAD; retain security, compatibility and regression coverage with a smaller non-duplicative test inventory.
- **Termux acceptance and rollout.** Validate a real 40/60/80-column portrait Termux session (including Nerd Font and fallback), tmux/SSH and resize; show ZIP and verified PYZ `check → apply → commit → session` using real receipts, plus GitHub Actions CI from pending through exact-HEAD qualification/failure and explicit log viewing. Compare the unified display against canonical machine JSON/native receipts; prove that closing/detaching the UI never cancels, reruns or falsely qualifies native work. Keep the old CI interface available as a compatibility fallback until parity and hosted/device acceptance pass, then switch the default with a documented migration path.

**Acceptance:** One shared *vertical* Rich Live UI is the primary native Termux interactive view for **both overlay execution and GitHub CI**; existing CI job/log/keyboard features are preserved; no unverified success, hidden native stdout/stderr, fullscreen Textual requirement, clipboard side effect, duplicate polling/session engine or regression loss. Exact-source CI, local PTY tests and real Termux device evidence must be reported separately. The HTML prototype does not count as native execution or device acceptance.

## Milestone 7 — Credential Doctor, installation repair and permission drift

**Outcome:** One read-only-first credential-health and recovery workflow for PATs, `gh`/SSH and GitHub App installations. A healthy secret store does not count as verified repository access. M7 builds on M4 without creating a separate GitHub API client or bypassing installation approvals.

- **Credential Doctor.** Discover the actually selected provider without printing secrets; validate token/session freshness, exact repository origin and head/base slugs, App installation identity, selected-repository coverage and operation-specific permissions. Show an actionable matrix for Git push, PR creation/updates, Actions read/write and workflow changes. Classify `VALID`, `EXPIRED`, `WRONG_INSTALLATION`, `MISSING_REPOSITORY`, `PERMISSION_DENIED`, `REQUIRES_APPROVAL`, `PROVIDER_UNAVAILABLE` and `UNVERIFIED` separately, never flatten them to success.
- **PAT verification and guided repair.** Probe the intended repository and required operation; distinguish fine-grained PAT repository selection, token expiry/revocation, SSO/organization restrictions and missing permissions. Provide precise instructions/links to update the token's repository selection and permissions, then re-probe. Devtool cannot secretly edit a PAT's GitHub authorization.
- **Authorized GitHub App recovery.** When an installation already grants the required management authority, discover and verify the replacement installation/repository selection, request a new restricted token, verify effective permissions and bind the restored identity to the specific repository. For missing installation or approval authority, use a guided GitHub authorization/installation flow and hard-stop until independently verified. Never silently elevate App permissions or reinstall an App on behalf of a user without the required GitHub consent.
- **Pre-delete and post-recreation gates.** For any repository rebuild/migration integrated with Devtool, run mandatory *pre-deletion feasibility checks* on the future repository owner/name, installation policy, existing secrets and dependent repositories; treat unverifiable credentials as a blocker before destructive operations. After recreation, prove that required PAT/App credentials actually access the recreated repository and that federation/Actions permissions work. The existence of an `MMRL_FEDERATION_TOKEN`-like secret is not proof of its repository access. Preserve migration/resumption evidence and do not call the restoration operationally complete until these checks pass.
- **Permission drift and identity health.** Detect installation removal/suspension, repository-selection changes, permission reductions, private-key rotation and credential expiry. A local read-only health dashboard and bounded optional scheduled monitoring should expose the precise missing capability; monitoring never automatically broadens permissions. Protect reports against credential/URL exfiltration, malicious redirects and partial GitHub outages.
- **Recovery safety and tests.** Reuse durable session and GitHub identity/reconciliation receipts. Negative coverage: deleted/recreated repository, same-name new repository ID, lost fork access, stale PAT selection, App permission regression, expired JWT, secret substitution, unauthorized repair request, provider uncertainty and failed acceptance gate. No forced rewriting of immutable overlay source pins.

**Acceptance:** Real and simulated negative credential matrices; successful authorized renewal or guided approval followed by a fresh, independently verified repository-specific capability probe; deletion/recreation hard-stop on absent authority; private receipts without secrets; exact head/base identity and zero implicit remote mutation. Credential validity is not equivalent to approved delivery or passed CI.

## Milestone 8 — Event-driven continuation and bot-owned PR maintenance

**Outcome:** Explicitly opted-in, source-bound continuation of existing delivery sessions when GitHub emits relevant events, plus bounded maintenance of PRs created/owned by Devtool's App. M8 never makes automation a blanket approval to mutate.

- **Secure event transport.** If an accessible trusted webhook receiver is configured, validate each `X-Hub-Signature-256` HMAC, installation/repository identity, event type, delivery ID and replay/deduplication journal before processing. Reject unrecognized/stale/ambiguous events and duplicate deliveries; keep secret material outside the repository and redact webhook payload logs. Termux-only deployments may use bounded polling/scheduled reconciliation instead; a local Termux process is not assumed to host a public webhook endpoint.
- **Explicit automation policy.** Allow operators to approve a named, finite continuation policy tied to exact repository, branch, commit/PR/session, allowed event types, permissible effects and expiry. An App credential or webhook delivery alone never authorizes a new Git mutation or Actions dispatch. Default to observe-only; mutating maintenance needs a separately approved policy and the existing durable intent ledger. Abort on head/base drift or revoked installation access.
- **Resume after credential expiry and interruption.** Re-mint installation tokens as needed; resolve existing local/remote journals, compare the actual GitHub state and continue only unperformed authorized steps. Unknown push/PR/dispatch outcomes require read-only reconciliation, never blind retry. Report stalled and blocked transitions in the M6 live view without pretending work runs in the background when no scheduler/receiver exists.
- **Safe PR maintenance.** For App-owned or explicitly opted-in PRs only: read-only review of checks and discussion, status/label/comment updates where authorized, bounded CI rerun/dispatch only when explicitly permitted, and source-pinned updates with proven non-force fast-forward behavior. Never rewrite user-owned work, edit a PR with a different actual head, auto-merge, approve own permissions, or make unlimited CI retry loops. Preserve audit events and exact provider response correlation.
- **Trigger/CI semantics.** Check the real workflow event/registration and attempt rather than assuming an App push triggers every workflow. Classify branch protection, missing workflow, rate limit, skipped/neutral, failed, cancelled and unregistered checks accurately; pass only under the committed required-CI policy. Deduplicate repeated and out-of-order webhooks.

**Acceptance:** Hosted authenticated webhook tests plus no-public-receiver Termux fallback; adversarial HMAC/replay/redirect tests; crash/expiry/duplicate-delivery/unknown-POST fault injection; approved bot-owned PR maintenance and exact-HEAD required CI at a real PR; no unapproved extra push, PR, dispatch or merge.

## Milestone 9 — Cross-repository federation and dependency delivery

**Outcome:** Coordinate linked native delivery sessions, PRs and CI dependencies across multiple GitHub repositories with separately attested GitHub App installations, without claiming cross-repository atomic Git transactions.

- **Federation plan.** Declare a bounded graph of repository owners/slugs, expected source HEAD/tree and target branch, head/base PR identity, required CI policies, dependency ordering, approved operations and eventual acceptance conditions. Detect cycles, missing repositories, implicit upstream substitutions and incompatible permissions *before* mutation. Pin both source Git object IDs and GitHub repository identities (including recreation/rename drift).
- **Least-privilege per repository.** Select/check an installation and operation-specific token for each repository; one authorized repository does not confer access to its dependents. Verify forks, repository selection, permissions, upstream base and policy at every dependency edge. Never share a token or private key between repositories or store it in a federation receipt.
- **Source-bound orchestration, not a new apply engine.** Delegate every local overlay to that repo's native transaction/validation/commit and every GitHub mutation to its existing delivery-session intent mechanism. The federation coordinator only plans, observes, gates dependencies and records correlations, without manual patches, force pushes, rebases, hidden merges or duplicate session engines. An unmet dependency blocks downstream mutation.
- **Failure and recovery.** Use durable per-repository sessions and a high-level correlation receipt showing `COMPLETED`, `WAITING`, `BLOCKED`, `UNKNOWN_REMOTE` and partial progress. Never represent a multi-repo sequence as all-or-nothing atomic; after partial success, reconcile independently and require explicit decisions for remaining authorized steps. Safe rollback applies only within the existing uncommitted native transaction boundaries.
- **Dependency builds and Actions.** Coordinate hosted builds and required CI by exact workflow/event/run attempt/PR HEAD per repository; block downstream delivery until verified gates pass. Support opt-in progress summaries, cross-repo PR references and read-only analytics in the M6 console; later event-driven handling may use M8, but source-bound polling remains supported.

**Acceptance:** Multi-repository integration with at least one forked upstream and one denied/missing installation; dependency-order and cycle tests; token and webhook isolation; failed/skipped CI and recreated-repository drift negatives; crash recovery without replay or overwriting unrelated work; verified hosted receipts for each repository, not one inferred aggregate green status.

## Validation strategy and required gates

| Lane | Runs where | Scope | Required evidence |
|---|---|---|---|
| **Local/Termux gate** | Native target runtime | Bounded manifest-declared smoke, file/source integrity, preflight, transaction/recovery | Native transaction ID, executable outcome, commit/session receipt; no full regression |
| **Hosted focused regression** | PR workflows | Parser, compatibility, source, runtime, session and GitHub negative cases; interactive PTY/storage integration | Exact PR HEAD + current run attempts/jobs and passing test outcomes |
| **Full repository regression** | Explicit hosted acceptance request | 24-shard (or current canonical) suite with source-bound summary | Separate correlated request and fail-closed aggregate; not implied by PR checks |
| **Device/release** | Actual device / release pipeline | Termux behavior, signing, deployment, release-specific gates | Separate evidence; never inferred from local or hosted checks |
| **M4 App auth/provider** | Local + hosted scoped integration | JWT/installation token rotation, exact repo membership, permission/SSH/PAT fallback, credential helper secrecy, fork access | Provider/API proofs, no leaked secrets, approved intent; no inferred CI qualification |
| **M7 credential recovery** | Controlled synthetic + authorized live fixture | PAT access/repair, App installation recovery, repo deletion/recreation, permission drift | Pre-deletion feasibility receipt, post-restore effective permission probe, explicit authorization |
| **M8 event automation** | Hosted receiver + Termux fallback | Signed webhooks, delivery ID dedup, bounded workflows, no accidental PR/dispatch writes | Correlated event IDs, approvals, unchanged source SHA, exact CI outcomes |
| **M9 federation** | Multi-repo hosted acceptance | Dependency DAG, fork identities, per-repo grants, partial failure, drift | Separate native sessions and exact required-CI receipts per repository |

**Global refusal gates:** malformed or untrusted executable; missing/mismatched SHA or blobs; incompatible runtime; dirty/unnamed target; source drift; conflicting session; failed or skipped required executable validation; ambiguous remote mutation; wrong PR HEAD; missing required CI policy when formal qualification is requested; skipped or missing required workflow; release claims without independent evidence; missing/expired/incorrect GitHub App installation; unverified selected-repository access; permission/SSO drift; secret exposure; forged or replayed webhook; unauthorized automated mutation; repository recreation without successful post-restore credential probe; unverified federation dependency or inferred cross-repository atomicity.

## Delivery format for each milestone

Deliver **one consolidated native, immutable, source-pinned overlay per milestone**, not stacked historical Pxx/HFxx hotfixes. For each deliverable report: repository/branch; starting and resulting HEAD/tree; launcher/runtime identity; exact file inventory; changed/consolidated/deleted checks and reasons; local/native validation; durable session; pushed PR and exact hosted CI; Termux executable command; remaining acceptance and limits. Use native recovery/session continuation after commit, not manual Git patches or reapplication.

## Definition of done

- Native ZIP/PYZ delivery can be completed without a launcher-specific orchestration path.
- Existing native engines and authoritative journals are reused, not duplicated.
- Source, runtime, artifact, validation, commit, session, PR and CI evidence remain independently verifiable.
- Push/PR/CI remain explicit, resumable and safe under partial failure.
- Old overlays and launcher v3.2 continue working without silent repinning.
- Termux performs only bounded smoke validation; hosted regression and on-demand full suites preserve security and regression assurance.
- All claimed acceptance is tied to real exact-HEAD receipts, with device/release claims kept separate.
- After M5, the vertical Rich Live console replaces the default GitHub CI terminal presentation and also covers overlay events, with proven Termux portrait/TTY behavior and preserved exact-source native evidence (M6).
- In M4, a verified App installation can authenticate scoped API/Git operations without overriding PAT, `gh` or SSH; operator approval, native journals and exact required-CI qualification remain separate.
- In M6, GitHub App identity, repository access, permission/expiry health and fork/base capability are shown in the shared vertical Rich Live view without displaying credentials or conferring approval.
- In M7, Credential Doctor demonstrates effective repository access and enforces pre-delete feasibility plus post-recreation credential acceptance; unauthorized installation changes remain guided, not automatic.
- In M8, optional signed-event or bounded polling continuation is deduplicated, source-bound, auditable and constrained by an explicit approved automation policy; bot-owned PR maintenance never rewrites user work.
- In M9, cross-repository delivery has per-repository source/credential/CI receipts and dependency gating, with partial failures reported rather than represented as a globally atomic success.

### M4 consolidated compatibility correction — Guardian receipt adoption

M4 includes the native committed-receipt validator event fix for Guardian
v0.12.5: trust only successful `artifact-validate-script` child outcomes and
retain the canonical `validation` manifest policy key. M3 continuity fixtures
now cover the real native event and reject aliases, failed/skipped children,
and malformed validation policy. Original Guardian recovery is a distinct
post-merge acceptance step; no original history/commit is rewritten.
