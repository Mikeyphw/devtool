# Devtool S7 P07 — final integration and release-readiness review

This phase concludes the **non-release** S7 consumer-adoption pilot with a
read-only, source-bound integration and merge-sequencing review. It extends P06,
**not** the existing `delivery session` push/PR/required-CI registrar. It adds
no automatic merge, release, signing, repo mutation, or physical-device action.

## Source evidence and scope

P07 runs on Devtool PR **#17** (`feature/devtool-delivery-s0-s1-20261114`)
and reuses the P06 module to independently recheck the three frozen consumer
PRs. Their required seven successful GitHub Actions workflows, latest
attempts, jobs, and required steps are validated again against their exact PR
head SHAs. Each real consumer must remain open/draft on the expected branch.

For all **four** PRs P07 obtains and cross-checks:

- PR head, base SHA, branch, originating repository, open/merged/draft state;
- current `main` SHA and compare `main...HEAD`, with a verified main merge
  base, positive ahead count and zero commits behind;
- GitHub's mergeable and mergeability-state assessment, which is evidence for
  *review*, not permission to merge;
- a second read of PR identities and `main` heads to refuse mid-audit drift.

Devtool source provenance is checked against the selected checkout's exact
`HEAD`, Git tree, clean worktree, canonical origin and provider Git commit
tree. Hosted PR checkout may be detached. P07 takes the new Devtool head as
an **explicit input**: it does **not** erroneously insist on the old P06 SHA
once P07 itself is committed. Consumer P03/P04/P05 pins remain fixed.

## Run

From the selected Devtool feature worktree, using authenticated `gh`:

```zsh
python3 -B -m devtool.delivery_integration_review \
  --repo "$HOME/Code/devtool-s7-p06" \
  --expected-head "$(git -C "$HOME/Code/devtool-s7-p06" rev-parse HEAD)" \
  --output "$HOME/.local/share/devtool/s7-p07-evidence" --json
```

The exact-head hosted CI workflow executes the same verifier and the original
P06 tests, then saves `s7-p07-evidence.json` and `s7-p07-readiness.md`.
Reports are 0600 files in a private 0700 directory, written atomically.
The report is a point-in-time snapshot and refuses inconsistent or incomplete
GitHub provider data. No GH write requests are made.

## Review and conditional merge order

1. `Mikeyphw/devtool` PR #17 — foundational canonical delivery implementation.
2. `Mikeyphw/scriptexts` PR #3 — standalone script consumer adoption.
3. `Mikeyphw/chroot-ctl` PR #1 — Go consumer, retaining GO17 purity and the
   separate dual-edition candidate workflow.
4. `Mikeyphw/xbsctl` PR #29 — Android consumer, retaining Go/module CI and
   the separate hosted API 36 debug acceptance.

**This is an order to consider after review, not an automated merge plan.**
Before each merge, owners must recheck current SHA/branch relationship,
required CI and branch protections, remove draft status, complete review, and
re-audit downstream dependencies. A later commit invalidates its earlier
source-bound evidence. No branch protection bypass or release approval is
suggested.

## Explicit outstanding gates

- P07's *own* hosted workflow cannot qualify itself while running; its
  successful status must be separately observed on the newly committed SHA.
- Required-CI registration in Devtool delivery sessions and branch/ruleset
  enforcement remain separate, not inferred from green GitHub workflow badges.
- Consumer CI workflows currently fetch reviewed Devtool S7 P02 commit
  `416d24140c7f7a4b9833d7cb19df50ca7e7363b8`, not a final release tag.
  Review this dependency before considering a release migration.
- No Android physical-device acceptance, production signing, final artifacts,
  release publication, or formal release qualification is established.
- All four PRs were in **draft** at the P07 baseline. Draft status alone
  prevents representing the sequence as merge-approved.

## Cleanup

P07 imports and retains P06's documented cleanup decisions. It does not
silently delete the scriptexts static workflow contract, chroot-ctl's
Go-native/dual-release gates, or XBS's existing Go/module workflow.
The old forbidden `tests/test_devtool_go_adoption.py` remains removed.

## Safety and CI

The new `.github/workflows/devtool-delivery-s7-p07.yml` runs Python 3.12 on
GitHub's hosted Ubuntu runner, with exactly pinned checkout/setup/upload
Actions and read-only `contents` permission. GitHub observation uses only
`gh api -X GET`. No local Gradle, root/device writes, PR creation, merge,
push, secrets update or release invocation occurs in this module. The
separately authorized delivery launcher calls Devtool's **existing** source-
bound transaction/session APIs to commit and reconcile this additive overlay.
