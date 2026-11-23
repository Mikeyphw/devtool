# GA-02 — Live registry and operation-specific App permission qualification

**Source:** GitHub PR #48, immutable preimage `d12658f6b2ce9b4b28639a194fbeb5397637cd05`, tree `cb37c61110107adcbba85ee0773db9e7691c81ba`.
**Ownership:** `github_relationships.py` (policy/CLI); `github_installations.py` (existing identity); `ga02_live_registry.py` (live observations and qualification); `github_actions.py` (existing Actions read transport extended to bounded account listing). M4 issues tokens; M7 diagnoses grants; GA-01 maps operations; Devtool native owns all mutations, history and CI.

## Trust boundary

Three **non-interchangeable** views are presented in `live-catalog`: (1) account-visible GitHub repositories, (2) explicitly enrolled policy repositories with freshly verified App installation identity or anonymous public metadata, and (3) explicitly passed local checkout paths with read-only Git origin/HEAD. Neither a visible repository, a local Git remote, a relationship, an alias nor stale metadata **ever** confers effective permission. The output is a read-only snapshot, not an authorization cache.

The account view is opt-in with `--account-auth env` or `--account-auth gh` (never `auto`, `app`, or anonymous). It uses `GitHubActionsClient.user_repositories`, bounded to 300 items, issued with the existing account credential resolver and no new transport. Account visibility may include private/collaborator/organization repositories, but only the explicitly enrolled nodes are allowed into App qualification.

The App view calls the existing `observe_installed` for each enrolled base/head node, checking real repository ID, owner numeric ID, installation ID, visibility and active App read token. Public nodes use anonymous GitHub metadata and cannot be promoted into App scopes.

`qualify-operation` performs GET-based M7 identity/read diagnostics and issues the existing M4 short-lived, repository-limited App token for exactly one GA-01 closed-world operation. It compares repo, owner, installation and App IDs across policy, independent repository observations, M7 and the scoped token receipt. **It never sends GitHub write requests.** Even if write-capable permission was demonstrated by successful token issuance, `write_authorized=false`, `write_executed=false`, `write_execution_verified=false`, `remote_mutation=false` remain mandatory.

## CLI

After a successful Devtool-native source apply, validation, commit and scoped delivery, and after preparing an owner-reviewed `devtool.ga02.relationships.v1` policy with pinned SHA-256 and its **current exact source commit**, use:

```text
python3 -B -m devtool.github_relationships live-catalog \
  --policy /absolute/owner-reviewed-policy.json --sha256 SHA256 \
  --source-sha SOURCE_COMMIT --account-auth gh --account-limit 100 \
  --checkout /absolute/path/to/existing/checkout --host termux

python3 -B -m devtool.github_relationships qualify-operation \
  --policy /absolute/owner-reviewed-policy.json --sha256 SHA256 \
  --source-sha SOURCE_COMMIT --repository Owner/repository \
  --operation delivery.pr.create
```

`--account-auth none` is the default and does not silently attempt account enumeration. `--checkout` is repeatable, explicitly bounded, never scanned recursively. `--alias NAME=Owner/repo` is a repeatable navigation-only label with case-insensitive collision refusal; labels never grant access. Absent, revoked, wrong-owner, suspended, permission-denied, private redacted-upstream and mismatched source states fail closed; the CLI emits a fixed refusal code and returns nonzero. It never prints tokens, JWT, private key paths or provider response bodies.

`EFFECTIVE_SCOPE_VERIFIED` is **read plus scoped token issuance**, not an actual GitHub write, PR acceptance, CI completion or release qualification. Live qualification must be rerun after source change, installation change or lost authorization; prior snapshots are historical evidence only. A failed or missing App installation is reported as BLOCKED, not swapped to a PAT/`gh` grant.

## Native and hosted acceptance

The source overlay is a *single* Devtool v3 native ZIP. Exact clean baseline and Git blob preimages are required; all changed files including native tests and documents are inventoried. Runtime validation includes GA-02 relationships/federation, GA-01 scoped auth and M7 diagnosis regressions, plus the dedicated live-registry suite. A native success produces a new commit and durable history/session; only then may `--deliver` perform approved PR delivery and new-head required hosted CI. Existing successful CI on old PR #48 HEAD is not qualification for a future SHA.

**Explicit remaining gates:** real App installation attached to the chosen owner/repository; private/fork/wrong-owner negatives on disposable repositories; live M7 drift after grant revocation/selection changes; exact PR #48 new-head required checks; 24-shard CI if required; independent GA-00 security, release and device qualifications. No Gradle may be run locally.
