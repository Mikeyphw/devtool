# QG01-CI24-F01 — G3R hosted action runtime pin correction

Status: **SOURCE CANDIDATE; native apply, PR source update, required CI and a fresh CI24 remain unverified**.

## Exact source and failed evidence

- Repository: `Mikeyphw/devtool`; branch `feature/devtool-r06-base-adoption`.
- Preimage HEAD: `038f9f9beade4bda9715979c79fdb699226b226b`; tree `bc6a53e388684243b6cc4a074342b6ffdf9ad5a3`.
- CI24 workflow `devtool-full-repository-pytest.yml`, GitHub run **45119410602**, attempt **1**, status **failure**, bound to the preimage SHA.
- Hosted matrix: **24/24** shards and **739/739** modules executed, 9211 tests, **27 failed**, 0 errors. Shard 23 failed, summary failed closed.
- All 27 failures belong to `tests/test_r39_r4_roadmap_gate_audit.py`. They originate in the source-audit violation `R39_R4_ACTION_RUNTIME_GAP` for two third-party/first-party runtime pins; the R37 pinned-actions assertion additionally fails because the whole-workflow audit is FAIL.

## Exact correction

In `.github/workflows/devtool-g3r-current-evidence.yml`, within `android-g1-hosted`, replace

- `actions/setup-java@v4` with `actions/setup-java@v5`; and
- `gradle/actions/setup-gradle@v4` with `gradle/actions/setup-gradle@v5`.

The `devtool.roadmap_gate_audit` policy already expects these versions. Verified upstream tags existed on 2026-11-23. No broad action-policy relaxation, no test skipping, no local Gradle, no changes to previously attested R37 SHA pins.

## Native adoption and CI qualification

Apply this data-only overlay exclusively with the trusted Devtool native overlay workflow after source SHA/tree, worktree cleanliness and payload preimage checks. Reuse original session history and all persistent worktrees without reset/rebase/prune. The QG01 original session `b1c9c66b3556417eb34771a61be38031` and CI24 failure run `45119410602` remain historic evidence and do not qualify the new commit.

A new committed HEAD requires its own native delivery session, exact PR HEAD verification, six required CI checks and **a separately authorized fresh CI24 run**. Then perform hosted Android G1, complete G3R 13 lanes and independent verification. Signing/device/release remain separate.

## Acceptance status

- Workflow source pin correction: SOURCE COMPLETE (preimage-bound).
- Devtool native check/apply: NOT RUN on Termux.
- Exact new source required CI / CI24: NOT RUN.
- Hosted G1 / G3R / FS03 / device / release: NOT RUN.
