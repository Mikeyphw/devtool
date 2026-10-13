# G7 — Optional CI integrations (source implementation; live acceptance open)

The original roadmap labels G7 **optional**. This implementation adds only opt-in,
provider-scoped extensions to the existing `actions` command. It does **not**
replace the GitHub Actions provider, CI scheduler, Android build chain, or release
qualification authority.

## Read-only historical metrics

`devtool actions extensions metrics --limit 30 --json`

Fetches up to 100 actual GitHub workflow runs for the selected repository.
Reports observed terminal conclusions, durations *only when parseable and plausible*,
and rerun counts *only when attempts are present*. No denominator is inferred for
missing observations. Samples are API-limited and not a statistical population
claim. A 403/404/429 remains a failure, not an empty history.

## Trusted runner inventory

`devtool actions extensions runners --trust-file ~/.config/devtool/trusted-runners.json --json`

A versioned opt-in file may list **repository-scoped, exact** runner ID, name,
and required label set:

```json
{
  "schema_version": 1,
  "repository": "Mikeyphw/devtool",
  "profiles": [{"id": 12345, "name": "my-runner", "labels": ["self-hosted", "linux"]}]
}
```

Devtool rechecks the selected GitHub repository's runner inventory and requires
an exact ID/name/labels match. Missing, duplicate, or truncated inventories fail
closed. This is **operator-configured trust**, not a remote hardware attestation.
No runner is thereby eligible for a protected/release workflow; **runner job
dispatch and execution have not been added** in G7. Their security review/live
acceptance remains an open optional extension.

## GitLab read-only pilot

Use `GITLAB_TOKEN` in the environment (never on the command line), then:

`devtool actions extensions gitlab-pipelines --enable-gitlab --gitlab-project-id 12345 --json`

Only `gitlab.com` (pinned HTTPS endpoint) is supported. This is **read-only
pipeline discovery** by numeric project ID with bounded result size and no
redirect following. No GitLab CI execution, artifact registry import, or
cross-provider source attestations are claimed. GitLab responses cannot make a
GitHub artifact or release qualified.

## Explicit draft-prerelease asset upload

**Read-only preview first:**

```sh
devtool actions extensions publish-plan \
  --registry-id SIGNED_REGISTRY_ID --tag v1.0.0 \
  --asset-name signed-hud.apk --expected-sha EXACT_40_HEX_SHA --json
```

**Only when the operator decides to upload:**

```sh
devtool actions extensions publish \
  --registry-id SIGNED_REGISTRY_ID --tag v1.0.0 \
  --asset-name signed-hud.apk --expected-sha EXACT_40_HEX_SHA \
  --allow-unqualified-upload --yes --json
```

Both operations independently rerun G6 signed→unsigned artifact lineage,
certificate/identity verification and SHA256, and verify the GitHub repo, source
SHA and a tag resolving *directly* to the expected commit. The release must
already exist, must be a **draft prerelease**, and the asset name must be unused
in a complete (under-100 assets) inventory. No tag, release, policy, or
GitHub secret is created/changed. Exactly one POST to the fixed GitHub asset
upload origin is attempted; uncertain results are never auto-retried. The
returned upload receipt is **accepted, not independently re-downloaded**.
The draft remains unpublished and `qualified_release` remains false.

Uploads are bounded to 32 MiB and `apk`/`aab` names. This opt-in action is
not intended for protected production releases. It is specifically useful for
sharing a personal project's locally signed, unqualified development builds.

## Qualification boundary

- Source and offline negative tests are required by the overlay.
- Real runner-access/permission errors, GitLab access, metrics pagination,
  draft publishing, and hosted CI remain live acceptance tasks.
- Runner dispatch/selection and GitLab CI execution are **not implemented**.
- Paid GitHub private-repository branch rules remain excluded by user decision.
- G0–G6 statuses and outstanding live qualification gaps are not rewritten.
