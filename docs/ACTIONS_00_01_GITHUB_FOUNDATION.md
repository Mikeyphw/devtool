# ACTIONS-00 + ACTIONS-01 — GitHub Actions foundation

**Scope:** baseline after V4 and read-only API/provider foundations. This is **not** approval to dispatch, cancel, rerun, retrieve artifacts, sign releases, or declare remote CI execution complete.

## ACTIONS-00: Source-bound baseline

- Initial source: Devtool V4 mirror-removal baseline, Git `755deb69fe3e303b0310abfcac38329dd6ec825f`, tree `91a65474dd11a31ed9cfc7204d1636705f73624e`.
- V1–V4 changes remain in place: Git nonreuse fingerprint opt-out, staged path batch detection, Git-only scheduler skip, opt-in task timings, Termux deferred LIFE-controlled retention, and removal of `/sdcard/.logs/devtool` writer and readers.
- V4 performance improvement is **reported from Termux** by the user, but not supported by a numeric post-V4 benchmark. The previous post-V3 Git dry-run measured 3.57964 seconds total with 3.08339 seconds in retention/mirroring.
- Canonical CLI source: `devtool/core_cli_schema.json`. Generated surfaces are rebuilt into `share/core-generated` and compared against deterministic fixed-point expectations.
- Existing V105 audits, known SSH provider failures on unmodified V3, and the unrelated log-summary environment-isolation failure are **not** repaired or silently certified by this phase.
- Validation gate: dedicated provider negative tests, parser/registry parity, checked-in completion/help fixed-point, compatibility tests, and an actual disposable-checkout overlay application.
- Source mutation gate: Devtool's source-bound transactional overlay with staged-file protection, validation and rollback; no direct repository writes in the installer.

## ACTIONS-01: Provider authority

- Provider: `devtool.github_actions.GitHubActionsClient`; HTTP GET only, pinned `https://api.github.com` and GitHub REST API version `2022-11-28`.
- Auth: in-memory `GH_TOKEN` then `GITHUB_TOKEN`; optional `gh auth token --hostname github.com`, `--auth env`, `--auth gh`, `--auth none`. Never persist tokens, print them, include them in API output, or accept a credentialed Git remote URL.
- Repository: explicitly `--github-repo owner/name` or validated `github.com` `origin` (HTTPS/SSH/scp/Git protocol). No arbitrary API host or off-site API redirects.
- Discovery: `GET /repos/{owner}/{repo}`, optional `GET /user` for viewer identity, paginated `GET /repos/{owner}/{repo}/actions/workflows`, and `GET /repos/{owner}/{repo}/actions/workflows/{id}`.
- Fail closed: repository identity mismatch, path injection, protocol mismatch, redirect attempts, invalid response bodies, API errors, and pagination outside the requested repository.
- Read bounds: response limited to 4 MiB each, up to ten workflow pages of up to 100 entries; HTTP timeout 15 seconds. Transport and HTTP errors are sanitized.
- No GitHub dispatch endpoint, write token scopes, remote run state, or subprocess-based Git mutations are introduced.

### Commands

```sh
# Network-free capability report
devtool -r "$HOME/Code/devtool" actions baseline --json

# Explicit public repository, no credentials
devtool actions status --github-repo cli/cli --auth none --json

# Use your repository's git origin, GH_TOKEN/GITHUB_TOKEN/gh credential
devtool -r "$HOME/Code/my-app" actions workflows --json

devtool actions workflow build.yml --github-repo owner/repository --json
```

`actions` is a read-only discovery namespace. It intentionally **does not register as an executable EXO/SSH provider**. ACTIONS-02 must establish dispatch correlation and canonical run/node ownership before provider execution capabilities can be activated.

### Credentials and required permissions

For private repositories, use a fine-grained PAT or GitHub App token with **Actions: read** repository permissions for workflow inspection (plus access to the selected repository). GitHub CLI credentials can be reused when present. Public repository requests can use `--auth none`; unauthenticated requests have lower rate limits. GitHub token permissions cannot be proven locally: successful API reads qualify access only to the specific endpoint requested.

### Open gates

- No live authenticated private-repository proof on Termux is included.
- No GitHub workflow dispatch, remote run correlation, artifacts, build/signing, cancellation, resume, or release-gate authority.
- No full V105 campaign requalification; unrelated failures are still open.
- No Android HUD APK rebuild (not in scope).
