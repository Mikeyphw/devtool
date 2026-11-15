# S7 P06 — cross-consumer qualification and cleanup audit

This phase installs a **read-only** Devtool qualification module. It makes no
new GitHub mutation engine or duplicate consumer command parser. Canonical
`delivery session` remains the only push/PR/required-CI orchestrator.

### Pinned real consumers

| Repository | PR | Exact head SHA | Required workflow IDs |
|---|---:|---|---|
| Mikeyphw/scriptexts | 3 | b77453ba15db824fc3f6d711c25df397d5845207 | 317669398, 418864667 |
| Mikeyphw/chroot-ctl | 1 | dc94ebfb4d0522cad8ab8930f0004ac27c4b58cf | 324594821, 418878280, 391110853 |
| Mikeyphw/xbsctl | 29 | 5c3f0da68bcec5606750ab12c13689fa83841a98 | 386774409, 418896455 |

A report is valid **only** if each PR is still open/draft on its exact head and
branch; every required **pull_request** workflow ID and name points to that
head, its latest run attempt completed successfully, all required jobs passed,
and required Go-native / Android SDK / debug build / APK hash/evidence steps
completed successfully. Failed/skipped required steps, missing jobs, incomplete
API pagination, or source drift refuse the cross-consumer seal.

Commands from within the Devtool source repository:

```zsh
python3 -m devtool.delivery_cross_consumer --json
python3 -m devtool.delivery_cross_consumer --output "$HOME/.local/share/devtool/s7-p06-receipts" --json
```

Only `gh api -X GET` is invoked by this module. It requires an authenticated
`gh` client (or `GH_TOKEN` with access to these public repositories). It never
dispatches/reruns checks, pushes, changes PRs, edits source, or performs local
Gradle, builds, releases, signing, or physical-device operations.

The report is a *snapshot*, not a continuously guaranteed state. Any later PR
head change invalidates the frozen source pins and requires an explicitly
reviewed, newly source-bound P06 version. Report output is private mode 0700
with atomic 0600 files and deterministic hash over verified provider rows.

### Cleanup decisions

- `scriptexts/scripts/devtool_actions_contract.py` is a consumer-owned static
  configuration check, **not** a GitHub orchestration launcher; retain it.
- `chroot-ctl/tests/test_devtool_go_adoption.py` was already deleted by P04 HF01
  to satisfy GO17. The Go-native test replaces it; do not restore Python files.
- Keep `chroot-ctl`'s dual-edition candidate pipeline and `xbsctl`'s existing
  Go/module workflow; neither duplicates S7's non-release adoption gate.
- The S7 P03/P04/P05 launcher scripts are delivery artifacts, not installed
  duplicate schedulers. Do not delete maintained source without confirmed
  semantic equivalence.

### Qualification boundaries

`hosted_cross_consumer_acceptance=passed` does **not** imply the canonical
Devtool delivery sessions for P04/P05 have registered formal required-CI
policies, or that Android real-device, signing, release, and merge are approved.
Those remain separate gates. PRs stay open/draft, and the installed P06 module
intentionally supplies no workflow dispatch or write path.
