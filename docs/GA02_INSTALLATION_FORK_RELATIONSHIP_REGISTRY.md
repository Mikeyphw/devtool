# GA-02 — Installation, fork, and relationship registry

**Source boundary:** built after GA-01 PR #46 commit `0aae64616ae9955a43bb084c8ae36c42a56a90b5`; source implementation candidate only. No real GitHub owner/admin changes, multi-installation acceptance, fork lifecycle mutation, release, or device operation is claimed.

## Implementation ownership

- `devtool.github_installations` extends, **does not replace**, the merged M4 short-lived installation-token provider (`github_app.AppInstallationProvider`) and M7 credentials diagnostics. Its installed-repository snapshot verifies the App's actual scoped token repository number, repository owner ID, installation/account ID, role, fork parent, suspension, selected/all membership and visibility. Its receipts never contain credentials and never claim write authority.
- `devtool.github_relationships` loads a SHA-256 pinned, explicitly owner-provided policy. The declared six *directed* relation types are `fork_of`, `upstream`, `library_provider`, `consumer`, `submodule`, and `package_dependency`. Every node pins repository, owner and installation IDs, access role and visibility. An observed relation must match both those pins and, for forks, GitHub's actual parent numeric identity. Inferred matching names are not acceptable evidence.
- A distinct `head` role uses only `DEVTOOL_GITHUB_APP_HEAD_*` and will not inherit `base` credentials. A no-install public upstream uses a bounded anonymous HTTPS GET transport; private/unknown repositories remain blocked, and public metadata never authorizes a write.
- `federation_app_auth.fetch_from_github_app` validates an enrolled, pinned `submodule` source *before* Git fetch when a relationship policy is opted into. Existing users without an explicit policy retain M4 source-lock fetch behavior.
- `delivery_session.plan` / session creation invoke the policy guard when explicitly configured. Cross-owner delivery requires a pinned source SHA, policy-declared `fork_of` edge, actual remote fork parent/owner identity, exact head/base branch SHA and observed protection. This read-only guard does not replace M5 mutation intents or App operation permission checks. Source drift blocks before a session is created.

## Explicit opt-in example

Save an owner-reviewed JSON document outside the repository or in a separately reviewed source policy. The following schema is illustrative; its IDs **must be actual GitHub numeric identities**, not copied from the example:

```json
{
  "schema":"devtool.ga02.relationships.v1",
  "policy_id":"owner-approved-v1",
  "source_sha":"0123456789abcdef0123456789abcdef01234567",
  "repositories":[
    {"repository":"Owner/source","repository_id":123,"owner_id":10,"role":"base","installation_id":456,"visibility":"private"},
    {"repository":"Other/fork","repository_id":789,"owner_id":11,"role":"head","installation_id":457,"visibility":"private"}
  ],
  "relations":[
    {"kind":"fork_of","source":"Other/fork","target":"Owner/source","policy_ref":"docs/owner-approvals.md"}
  ]
}
```

Use the corresponding explicit environment configuration:

```text
DEVTOOL_GA02_RELATIONSHIPS_FILE=/absolute/path/to/owner-reviewed.json
DEVTOOL_GA02_RELATIONSHIPS_SHA256=<actual SHA-256 of the exact file>
DEVTOOL_GA02_FEDERATION_OWNER=Owner/source
```

For an opt-in child repository `federation.source.fetch`, use `submodule` from the declared owner to the child. Policy file/content digest must match; partial configuration refuses. For cross-owner head, owner must separately configure the `DEVTOOL_GITHUB_APP_HEAD_*` installation. There is **no** PAT, SSH, unauthenticated private fallback, automatic installation creation or implicit fork-write grant.

Read-only commands:

```text
python3 -B -m devtool.github_relationships inspect --policy /path/to/owner-reviewed.json --sha256 EXACT_SHA
python3 -B -m devtool.github_relationships verify-observation --policy /path/to/owner-reviewed.json --sha256 EXACT_SHA --repository Owner/source
```

These commands cannot create relationships or mutate GitHub. The opted-in source tree must be kept in sync with the policy's `source_sha` and exact source origin; re-read and reapprove after commits/renames/transfers.

## Source/acceptance distinction and security gates

The deterministic native test suite includes missing/suspended installation, revoked selection, numeric ID drift, owner transfer, rename, delete/recreate, wrong fork parent, unauthorized cross-account head, private public-fallback, stale policy digest, stale base SHA, unprotected branch, duplicate JSON keys, and no-policy backward compatibility. Tests include a source-lock-controlled federation fetch refusal with no Git operation. The source-only validation does **not** establish actual live private installation access, remote App-head token permissions, owner approval, private upstream membership, or a real authorized hostile GitHub test. Those are GA-02 live-security acceptance obligations and remain pending.

GA-00 shared signing/governance are maintained in a separate deferrable ledger, enforced at their actual signing/merge/release boundaries. Do not redispatch GA-00/01 CI24 to validate GA-02; CI24 must bind to the **new GA-02 commit** once that commit is actually delivered through native Devtool.
