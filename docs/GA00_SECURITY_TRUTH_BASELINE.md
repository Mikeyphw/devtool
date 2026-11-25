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
**explicit approval profile**, **deployment branches restricted to protected branches**
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

## W2 explicit signing approval profiles

The existing strict `multi-reviewer` default requires self-review prevention. An owner may explicitly select `single-owner` only for a personal repository with a live numeric owner identity, one required User reviewer matching that owner, and explicit `prevent_self_review=false`. Both require protected branches only and admin bypass disabled. No automatic approval, environment creation, secret movement, release publication or bypass is provided.

Read-only verification: `python3 -B -m devtool.ga00_environment --repo Mikeyphw/devtool --approval-profile single-owner`. Provisioning and readiness: use `actions release setup` / `actions release readiness` with `--approval-profile single-owner`. Private schema-2 release policy binds the owner numeric ID and profile; existing schema-1 policies remain strict. An existing conflicting policy is refused, not silently replaced. Recheck actual deployment admission and certificate independently.


## P09 owner projection and ACTIONS-09 integration repair (2026-11-25)

The production standalone environment validator returned `VERIFIED_CONFIGURATION`,
while the Actions preview received `owner_reviewer_id=null` because the canonical
GitHub repository projection discarded `owner`. The canonical client now retains
only a validated login, numeric ID and account type; missing or invalid owner
metadata does not establish ownership. Organization, wrong reviewer and owner
drift continue to fail the single-owner profile; strict multi-reviewer behavior
is unchanged. The actual Actions parser/dispatch, HTTP normalization, private-file
LIFE identity resolver, real disposable PKCS12 keytool inspection and secret journal
are covered together; remote calls are test fixtures, not production proof.

The existing source-pinned workflow path is normalized to GitHub's filename lookup
and the returned full path must match. The single-owner ACTIONS-08 verifier additionally
requires the first completed workflow attempt, one actual approval review from the
pinned User for the exact environment ID/name, and no ambiguous/paginated evidence.
Current configuration is not deployment approval. GitHub review history is run-scoped;
reruns cannot inherit this qualification. See GitHub REST workflow-run review history.
The setup path rechecks environment/owner protection before every secret write and
before private policy creation. No live environment mutation, secret rotation,
deployment approval or release publication occurs in this repair.

Production readback (owner supplied): single-owner environment configuration verified;
LIFE identity `devtool-android-release` registered with certificate SHA-256
`a167f33d8781c5675464011c9f975b343d938632db8b4dfc414e8322e890e99c`.
Preserve that identity. Provision only after this source repair is merged and the
actual Actions preview agrees with the standalone verdict. P17 release acceptance
remains separate, requiring actual protected deployment and signed provenance.
