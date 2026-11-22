# GA-00 — security and evidence baseline (source candidate)

This source candidate adds fail-closed App Git remote verification, separate
credential-free Android packaging lanes, a durable intent-before-secret-upload
journal, typed LIFE secret-provider refs, and a read-only governance preview.

**Authority:** M4 App and native delivery journals remain the only delivery
execution authorities. `devtool.ga00_secret_journal` governs only Actions-09
secret writes; it does not attest remote secret values or release signing.

## Observed capability boundaries

- The installation provider verifies its selected repository using the real
  GitHub API. A local source test does not prove that the current App is installed
  or grants permissions; use M7 Credential Doctor with approved read access.
- Git App transport refuses unsafe effective fetch/push URLs, local Git rewrite
  configs, URL parameters and unapproved Git argument forms. Native delivery
  still owns explicit authorization, push intents, and read-only reconciliation.
- Debug/unsigned packaging steps have no GitHub signing-secret bindings. Hosted
  signing selects the `devtool-android-signing` job environment; the repository
  owner MUST configure required reviewers/protected deployment policies for it
  (GitHub may auto-create an unprotected environment if absent). Actual hosted
  negative tests are still required before production release claims. This
  source patch does not auto-configure environment protection, move repository
  secrets, or sign keys.
- Actions-09 secret updates create owner-only intent and acknowledgement events
  before and after each upload. If one upload has uncertain outcome, subsequent
  invocations refuse automatic retry and require private journal + GitHub
  metadata + signer reconciliation. Secret names are evidence of metadata only.
- LIFE accepts only opaque `fd:N`, `local:ENV_NAME`, `keyring:ID` or
  `secret-provider:ID` references at remote projection boundaries. It never
  transports commands, tokens or filesystem private paths as provider refs.

## Governance diagnostic (read only)

Use `python3 -u -B -m devtool.ga00_governance --repository Mikeyphw/devtool
--repo . --live` from a verified local checkout with `gh` already authenticated.
The audit uses GitHub GET calls only, compares the exact committed six-workflow
policy against observed required-check contexts, and intentionally concludes
`INCONCLUSIVE_OWNER_REVIEW_REQUIRED` until a human confirms actual equivalence.
Provide `--rulesets-json /path/to/rulesets.json` for offline fixture comparison.
No ruleset, approval count or check context is modified.

## Pending independent gates

- Native `check`/`apply`/commit/session/PR lifecycle on an exact verified Termux
  worktree, followed by exact-head required hosted workflows and separate CI24
  when required by the native contract.
- Live App-authenticated Git push/PR with intentionally adversarial Git config
  (only on a disposable repository), and real installation permission probe.
- GitHub hosted debug, local unsigned and protected signed release paths,
  including runtime environment admission, secret non-exposure, signing
  fingerprint verification, and action job conditions. Do not use production
  signing keys for pilot or negative tests.
- Inspect any existing private Actions-09 journals before secret rotation.
  No automated recovery of an uncertain write is provided.

No merge, signing, release, branch protection mutation, PAT revocation, or
Android device operation is authorized by this source candidate.


## GA-00 P09/P15 follow-up: external owner configuration and safe migration

**Release signing secrets are ENVIRONMENT-scoped only.** ACTIONS-09 now reads
`gh secret list -R OWNER/REPO --env devtool-android-signing` and writes
`gh secret set SECRET -R OWNER/REPO --env devtool-android-signing`.
Legacy repository-level signing secret names (the four `DEVTOOL_RELEASE_*` names)
**block** preparation: the owner must migrate/revoke them independently after
checking consumers. The operator never deletes, rewrites or silently rotates
repository secrets. GitHub returns metadata, not decrypted secret values.

Required signing environment: `devtool-android-signing`. The owner's GitHub
Settings > Environments configuration must have **required reviewers**,
**prevent self review**, **deployment branches restricted to protected branches**
and **administrator bypass disabled**. The read-only Devtool check uses the
GitHub environment API; unavailable fields, unprotected branches or missing
approval are **NOT VERIFIED**, not PASS. A YAML environment label alone can
auto-create an unprotected environment. Actual deployment approval and signed
artifact verification are separate requirements. The current public-repository
GitHub plan may support the required reviewer policy; verify the current plan.

Branch governance remains a **different** owner action. ACTIONS-09 release
setup cannot POST rulesets, even with `--yes`. Use the read-only
`python3 -B -m devtool.ga00_governance_approval preview --repository OWNER/REPO
--branch main` to inspect current source, rulesets and proposed additive rules.
Then, only if necessary, a separate owner/admin may run
`python3 -B -m devtool.ga00_governance_approval approve --repository OWNER/REPO
--branch main --owner-login EXACT_LOGIN --approval-digest EXACT_PREVIEW_SHA256 --yes`.
This requires live exact digest revalidation, a private fsynced intent before POST,
and refuses automatic retry if the POST outcome cannot be classified. A preview
does not enforce the six committed required CI workflows and this additive rule
only blocks deletion and non-fast-forward changes. Owner must independently
review required status-check contexts and approving review requirements.

### Source scope vs environment acceptance

Local adversarial tests cover absent/malformed environment rules, repo-wide
secret leakage, non-admin actions, stale approval digests, failed/ambiguous POST
and idempotent refusal. These are **simulated API tests**. An actual authorized
GitHub actor must run the read-only environment and governance checks and
record redacted receipts. Actual App installation grants, secret value provenance,
protected signing approvals and live signed output cannot be certified by unit
tests or earlier CI24 on a different source SHA. P09 and P15 source paths are
implemented; external configuration/acceptance remains PENDING ENVIRONMENT.
