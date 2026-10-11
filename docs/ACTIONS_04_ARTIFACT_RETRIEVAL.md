# ACTIONS-04 — GitHub Actions artifact retrieval

This phase adds artifact discovery, bounded ZIP download, digest verification, local re-verification, and canonical Devtool artifact-registry records. It does not qualify an APK, validate signing, extract files, or publish releases.

## Repository identity

`--github-repo` is optional. For a local GitHub repository, the selected `-r` workspace's `remote.origin.url` is already parsed and validated as GitHub `OWNER/REPO`. Explicit `--github-repo` remains available for ambiguous origins, forks, or non-GitHub remotes. Request-ID-based operations stay pinned to the repository and run ID recorded in the dispatch receipt.

## Commands

```sh
devtool -r "$HOME/Code/skybind" actions runs --json
devtool -r "$HOME/Code/skybind" actions artifacts --run-id RUN_ID --json
devtool -r "$HOME/Code/skybind" actions download --run-id RUN_ID --artifact-id ARTIFACT_ID --json
devtool -r "$HOME/Code/skybind" actions verify --run-id RUN_ID --artifact-id ARTIFACT_ID --json
```

`--request-id` can replace `--run-id` when a correlated ACTIONS-02 dispatch receipt exists. Artifact downloads are stored unextracted under `<repo>/.devtool/artifacts/github-downloads/`; metadata is registered through the canonical `.devtool/artifacts/registry.json` append-only interface.

## Safety model

- The run and artifact are retrieved from the selected repository using GitHub's API. Download requires matching run ID and `head_sha`, and a valid published SHA-256 digest; absent digests fail closed.
- The GitHub bearer token goes only to `api.github.com`. The short-lived signed redirect is opened without credentials and only for allowlisted GitHub Actions / Azure Blob hosts, without following further redirects.
- The compressed archive is capped at 256 MiB and checked before committing to storage. ZIP members are inspected and decompressed in memory-bounded chunks to verify CRC; traversal names, symlinks, special files, duplicates, encryption and oversized expansion are rejected. No extraction is performed.
- Existing downloads are never overwritten. The registry stores repository, run ID, attempt, workflow ID, remote commit, artifact ID, verified digest and `qualified_release=false`. `verify` recomputes the archive digest and validates it against current GitHub artifact metadata.
- A GitHub workflow marked `success` is not proof of artifact signing, APK correctness, release provenance or release qualification. ACTIONS-05/08 implement those independent gates.

## Boundaries

ACTIONS-04 does not implement Android-specific installable APK verification, signing continuity, AAB analysis, artifact promotion, GitHub Release publication, or background polling. A real live GitHub artifact download on Termux is still a native gate after installing the source-bound overlay.
