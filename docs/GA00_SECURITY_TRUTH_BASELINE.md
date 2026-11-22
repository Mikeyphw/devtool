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
