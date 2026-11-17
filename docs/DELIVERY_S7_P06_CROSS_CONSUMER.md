# S7 P06 — historical consumer hosted acceptance

P06 performs read-only verification of **exact historical consumer PR source commits** and their hosted workflow, job, attempt and required-step evidence. It does not check pending merge eligibility and must not treat a completed historical merge as failure.

The three pinned consumer PRs have already been merged. Their PR state is accepted only if GitHub reports a closed, merged PR with a nonempty `merged_at`, valid merge-commit SHA, the same pinned head SHA, correct head branch and same-repository `main` base. A still-open draft with matching source may also be inspected; a closed-unmerged PR, forged merge evidence, unexpected branch, fork, source drift or failed workflow is rejected.

The frozen workflow IDs, latest source-bound run attempts, complete job inventories and required evidence steps remain mandatory. Historical acceptance says those exact PR commits passed the specified checks at the time. It does **not** prove today's `main` content, current CI, release signing, branch-policy compliance or physical-device qualification.

## Regression and hosted checks

- `python3 -B -m unittest discover -s tests -p 'test_delivery_cross_consumer.py' -v`
- `.github/workflows/devtool-delivery-s7-p06.yml`: offline negative tests, then authenticated read-only provider evidence and private JSON/Markdown receipts

P07 consumes this gate as the single source of historical consumer evidence. Do not reintroduce an `open`/`draft` prerequisite for already merged PRs, remove exact-SHA evidence checks, or mistake a skipped optional job for a required-job pass.
