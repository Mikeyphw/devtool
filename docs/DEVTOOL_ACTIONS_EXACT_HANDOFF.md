# ACTIONS-UX02 — Exact remote review and archive handoff

ACTIONS-UX01 displays **local** histories only. UX02 adds two deliberately explicit
operations without introducing a second GitHub provider, job scheduler, or
artifact registry. They work through the existing ACTIONS-02/03/04/06 APIs.

## Review an existing correlated request

```sh
python3 -m devtool -r . actions review --request-id REQUEST32 --json
```

The request ID must name an existing durable ACTIONS-02 dispatch receipt.
The review checks repository, source SHA, workflow ID, dispatch event, branch,
exact run attempt and terminal job statuses. Missing/unknown correlation produces
`uncorrelated` (exit 2), never an inferred run or automatic redispatch. An
incomplete run likewise exits 2; a failed or unproven run exits 1; verified
success exits 0. **Verified remote success is not a release seal.**

## Collect a verified ZIP by exact artifact name

```sh
python3 -m devtool -r . actions collect --request-id REQUEST32 \
  --artifact-name EXACT_ARTIFACT_NAME --yes --json
```

Collection refuses unless the run passes review, has exactly one matching named
artifact, and the artifact belongs to the exact SHA, run and attempt. The
canonical ACTIONS-04 ZIP downloader verifies SHA-256, ZIP safety and registry
identity. Previously verified archives are reused; any checksum failure is
fatal and does not prompt a replacement download. One more review after
collection prevents the operation from reporting success after an observed
remote rerun. The archive remains **unextracted, unsigned/unqualified**, and
is **not** automatically promoted, installed or released. Running `review`
does not write an observation journal.

## Existing reviewed dispatch and source-bound DAG

The existing pipeline CLI remains the execution authority, e.g.:

```sh
python3 -m devtool -r . actions pipeline plan --file devtool-pipeline.json
python3 -m devtool -r . actions pipeline run --file devtool-pipeline.json --yes
python3 -m devtool -r . actions pipeline status --pipeline-id PIPELINE32
python3 -m devtool -r . actions pipeline resume --pipeline-id PIPELINE32 --yes
```

UX02 tightens `github-dispatch` DAG nodes: the remote ref must still match the
frozen local HEAD **before** any non-idempotent POST. A definitive pre-POST
mismatch is `failure`, while a request with uncertain POST outcome remains
`uncertain` and must not be automatically retried. Native `local-invoke` nodes
still delegate to Devtool, and `github-archive` nodes still use the canonical
registry. The pipeline result is never release qualification.

`devtoolw` retains its existing canonical wrapper behavior; no parallel
wrapper implementation or project-specific hardcoded targets are added.

## Boundaries

- Read/review/collection do not automatically launch or cancel remote jobs.
- No implicit latest run, guessed request ID, unverified archive extraction,
  automatic retry, token journaling, release publication, or historical
  qualification rerun.
- GitHub API availability and permissions remain external runtime inputs;
  the offline test and native acceptance probes use disposable mocks only.
- H1/H2 and the six independent substrate/release obligations stay open.
