# GIT-FRX-12 — Multi-Repository Campaign Stacks and Range-Diff

## Scope delivered

GIT-FRX-12 adds persistent multi-repository campaign generations over the existing federation plan and frozen-lock authorities. Campaign state lives under Devtool state storage; Git generation anchors are private refs under `refs/devtool/campaigns/<campaign>/repos/<repo>/generations/<n>`.

A campaign freezes the selected workflow/group contract identity, including Y-series DAG dependencies, artifact inputs, integration policy, publish-barrier identity, capability graph, and federation lock identity. Every checkpoint records the per-repository commit, repository identity, symbolic head, generation ref, and lock-generation evidence. Contract drift between generations fails closed and requires a new campaign.

`git range-diff` is evaluated per repository from the campaign base generation to two selected revised generations, producing digest-addressed cross-repository comparison evidence without pushing or rewriting user refs.

Campaign start also captures GIT-FRX-13 dirty-state snapshots so campaign-local state can be restored safely. Restoration across a changed HEAD validates conflicts first and rebases only non-overlapping staged/untracked state.

## Public surface

- `devtool git campaign start ID --workflow NAME|--group NAME [--json]`
- `devtool git campaign checkpoint ID [--json]`
- `devtool git campaign range-diff ID --old N --new N [--json]`
- `devtool git campaign status ID [--json]`
- `devtool git campaign restore-dirty ID [--repository NAME] [--json]`
- Self-hosted target-local fixture: `git-frx12-campaign-fixture`
- First-class test identity: `roadmap-git-frx12`
- Workflow identity: `roadmap-git-frx12`

## Audit evidence

- Merged focused milestone contracts: 15/15 passed after hardening.
- GIT-FRX-01..13 regression family: 155/155 passed.
- Core FRX regression family: 119/119 passed.
- X/Y federation regression family: 113/113 passed.
- SSH-02/03 evidence boundary: 38/38 passed.
- Native merged workflow `roadmap-git-frx11-13`: 9/9 stages, 57/57 tests, 0 warnings, 0 errors.
- Final campaign fixture created generations 0/1/2 for two repositories and produced a non-empty range-diff report digest with `automatic_push=false`.

## Promise ledger

- campaign start/checkpoint lifecycle: delivered;
- private generation refs: delivered;
- per-repository stack generations: delivered;
- multi-repository `range-diff`: delivered;
- federation lock generations: delivered;
- Y-series contract identity: delivered;
- contract-drift rejection: delivered;
- dirty-state recovery bridge to GIT-FRX-13: delivered;
- no automatic push: preserved.
