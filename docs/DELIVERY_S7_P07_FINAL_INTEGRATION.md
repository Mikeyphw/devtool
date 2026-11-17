# S7 P07 — current Devtool PR integration review

P07 v2 is a read-only evidence generator for **the current Devtool source** plus the **already merged** consumer acceptance verified by P06. It no longer assumes historical Devtool PR #17 or consumer PRs #3/#1/#29 are pending merges.

## Two explicit source modes

For a GitHub `pull_request` run, the workflow passes `${{ github.event.pull_request.number }}` to `--pr-number` and `${{ github.head_ref }}` to `--head-branch`. P07 checks the current PR is open, its same-repository head matches the exact checkout SHA and supplied branch, the base targets `main` at the current hosted main SHA, and `main...HEAD` is a provable fast-forward relation. It rechecks the PR and main before emitting evidence.

For manual `workflow_dispatch`, the PR number is zero and the head branch empty. That mode is accepted **only** when the checked-out SHA matches hosted `main`; it cannot be used to attest a feature PR without its identity. Both modes bind the checked-out Git tree to GitHub's commit tree when `--repo` is supplied.

P06's historical consumer evidence must still validate the original source SHAs, PR merge provenance, workflow IDs, successful runs/jobs/required steps and complete pagination. P07 rechecks consumer PR provenance after the current-source read.

## Security boundaries

The generated receipt always has `merge_permitted=false`, `auto_merge_permitted=false`, `release_qualified=false`, `physical_device_qualified=false` and `devtool_delivery_session_ci_qualified=false`. A P07 pass is **not** a branch-policy check or authorization to merge/release. CI failures, source divergence, provider races, tampered historical evidence, closed current PRs, unexpected forks and unproven manual-dispatch HEADs all fail closed.

Run the focused offline tests with:

```
python3 -B -m unittest discover -s tests -p 'test_delivery_cross_consumer.py' -v
python3 -B -m unittest discover -s tests -p 'test_delivery_integration_review.py' -v
```

The hosted workflow writes private, durable JSON and Markdown receipts. The old all-PRs-are-unmerged merge-sequencing gate and its duplicative P06 unit-test invocation in P07 have been retired; P06 retains its own tests and P07 still invokes P06's full source-bound evidence verifier.

The receipt schema is `devtool.delivery-integration-review.v2` because the obsolete v1 multi-PR merge-sequence layout was intentionally removed. Consumers must not interpret it as a v1 merge-readiness receipt.
