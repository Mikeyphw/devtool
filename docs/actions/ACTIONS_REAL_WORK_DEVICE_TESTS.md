# Devtool Actions — testing it with real work (G0–G8)

**This is a practical personal checklist, not a certification program.** Use Devtool while working on your projects; do one small case when you have a reason to use that feature. An "okay" screenshot is useful, but collect the exact run ID/commit/hash for remote cases so we can diagnose surprises later.

Start from the installed **G8 + operational-receipt hotfix**. This guide is for a rooted Android/Termux phone with the existing Devtool repo in `~/Code/devtool`. Keep GitHub's private-repository paid protection requirements **out of scope**. Do not copy signing keys or paste tokens into test files.

## One-minute setup (generates your JSON)

```sh
cd "$HOME/Code/devtool" || exit 1
OUT="$HOME/.local/state/devtool/real-work/$(date +%Y%m%d-%H%M%S)"
python3 tools/actions_real_work_tests.py init --repo "$PWD" --out "$OUT"
echo "Evidence folder: $OUT"
```

The script creates private `results.json` with **10 simple cases**; everything starts `not-run`. It **does not execute builds, dispatch GitHub jobs, read keystores, change source, or declare release qualification**. Example of a completed entry:

```json
{
  "G5_MATRIX": {
    "status": "passed",
    "run_id": 123456789,
    "artifact_sha256": null,
    "note": "8/8 Linux/Windows matrix cells completed; receipt collection succeeded"
  }
}
```

After any real operation, mark the result:

```sh
python3 tools/actions_real_work_tests.py mark --out "$OUT" \
  --case G5_MATRIX --status passed --run-id 123456789 \
  --note '8/8 matrix cells; ci check and collect worked'
python3 tools/actions_real_work_tests.py summary --out "$OUT"
```

A case only passes after you observed its expected behavior. If you didn't have GTK, GitLab, a trusted runner, bundletool or a signing identity, use **`blocked`** or **`skipped`**, not passed. Keep remote JSON output in your private evidence folder if helpful, but skim it for tokens and secret-containing error messages first.

## First session: everything you already use (10–20 minutes)

1. **G0 baseline:** Run a real everyday `devtool test`, `devtool invoke` or normal native Termux Android debug build from the project you're currently changing. Record command, exit code and whether the result was expected. For Android, keep the known working `android_hud` debug configuration and don't overwrite your signed APK. The old debug build was a regression baseline; this is a *fresh* confirmation.
2. **G1 read-only:** Run `devtool actions diagnose --json` and `devtool actions workflows --json`. Check your private repository appears and a workflow is identifiable. Review an existing run rather than dispatching a new one solely for a test.
3. **G7 metrics:** Run `devtool actions extensions metrics --limit 10 --json`. Verify nonempty observations if runs exist, and avoid treating unknown durations as zero.
4. **G8 reconciliation:** Run `devtool actions qualification report --json` and `devtool actions qualification check --json`. Source contracts should match; pending live/historical gates should remain pending. `--require-complete` should still fail until genuinely resolved—**that is expected**.

## Second session: the actual CI work (when you are changing code)

**G5 — polyglot GitHub matrix (real hosted Linux/Windows):** After committing and pushing your Devtool changes (check the remote HEAD matches local), try a cheap subset first:

```sh
devtool actions ci plan --ref main --languages python,go --systems ubuntu-latest --json
devtool actions ci run --ref main --languages python,go --systems ubuntu-latest --yes --json
# Copy RUN_ID and exact FULL_SHA from the real response, then:
devtool actions ci check --run-id RUN_ID --expected-sha FULL_SHA \
  --languages python,go --systems ubuntu-latest --json
devtool actions ci collect --run-id RUN_ID --expected-sha FULL_SHA \
  --languages python,go --systems ubuntu-latest --download --json
```

If the subset works, repeat **without** `--languages`/`--systems` to run all eight combinations (Python/Go/Rust/Java × Linux/Windows). Inspect each actual job and its receipt. One missing/failed cell must block `collect`. Record source SHA, run ID, attempt, artifact hashes, and whether a real Windows/Rust cell ran. The G5 fixture proves its own smoke program, not arbitrary user projects.

**G4 — actual Android AAB (only when you want a bundle):** Push the current Devtool source, run `devtool actions android aab-plan --ref main --json`, then `aab-run --ref main --yes --json`. When successful, locate the exact artifact and run ID in GitHub/Devtool; check/import using `aab-check` and `aab-import --download --run-id RUN_ID --artifact-id ARTIFACT_ID --expected-sha FULL_SHA --json`. **Prerequisite:** obtain a reviewed, compatible `bundletool.jar` and set `DEVTOOL_BUNDLETOOL_JAR` to its actual path. Use `aab-sign --registry-id ID --json` for the plan, then `--yes` only if your existing LIFE identity is correct. An AAB is **not an installable APK**: use it to validate bundle generation and local signing; keep the separately signed APK for actual HUD installation. Do not call it a protected release.

**G6 — local signing evidence during normal app updates:** Run `devtool actions release local-evidence --registry-id SIGNED_ID --expected-sha FULL_SHA --json` for an actual *newly signed* APK/AAB. Verify the existing LIFE signer certificate matches. Never edit the registry or mutate the signed original to create a negative test; use the offline test suite for tamper negatives.

## Third session: watch Devtool work (small UI + pipeline exercise)

**G2 DAG:** Start with a read-only plan: `devtool actions pipeline plan --file docs/templates/actions-pipeline-live-hud.json --json`. Read the template and its workflow inputs before running anything. The template may need copying into a committed repo path and updating for your real target. Use `pipeline run --file YOUR_COMMITTED_MANIFEST --yes --json` only when each action is safe and you genuinely want those GitHub jobs. Keep the returned `pipeline_id`; **never dispatch again just because a branch is waiting**. Observe with `pipeline status --pipeline-id ID --json`, then use `pipeline resume --pipeline-id ID --yes --json` only when status warrants it. To test a stop request, create a **separate, harmless** pipeline and call `pipeline cancel --pipeline-id ID --yes`; verify no new nodes schedule. Do not expect remote GitHub jobs or existing local child processes to be killed.

**G3 UI:** On your phone, open `devtool actions pipeline status --pipeline-id ID` **without** `--json` inside an interactive Termux terminal. Verify nodes/IDs, `r` refresh, `q` close, and narrow portrait layout. On the installed Android HUD, build/review the same pipeline cancel command and check that it warns it only stops future scheduling; test the import/copy path. GTK is optional: use it on your Linux PC when convenient. A view that fails to open is a real failure; do not turn missing GTK into an Android failure.

## Fourth session: unusual problems and optional providers

- **G1 negatives:** collect safe, *naturally occurring* permission failures, rate-limit responses, ref changes or expired artifacts. You don't have to force a 401/403/429. Never reveal your token, and never blindly replay an HTTP 204/unknown-result dispatch. Record `blocked` if none occurs.
- **G7 runners:** `devtool actions extensions runners --json` reads the available runners. An optional JSON trust profile must match an actual runner's ID/name/labels; it does not authorize production releases or schedule a self-hosted job.
- **G7 GitLab:** only test if you already use GitLab. Its read-only query requires `GITLAB_TOKEN` in the environment and `--enable-gitlab --gitlab-project-id NUMBER`. Don't put the token in the command line or evidence JSON.
- **G7 publish-plan:** optional only if you already created a GitHub **draft prerelease** for the exact commit and actually want to share a signed candidate. Start with `publish-plan`, not `publish`. Uploading a development APK does not mean the release is qualified.
- **G8 old regressions:** running `python3 -m pytest -q tests/test_artifact_v2.py tests/test_actions_g08_operational_drift.py` from a *clean checkout* verifies the isolated-apply repair. The huge legacy V105 suite is still a separate historical project; do not use this small test to say it is closed.

## What to send back after each session

Share the test case name, status, exact commit SHA, run ID and attempt (if GitHub), the **safe** error text on failure, and a screenshot if UI mattered. For APK/AAB, include artifact file hash and registry ID—but **never** your keystore, signing passwords, tokens, private configuration or secret-bearing environment. You can share a redacted `results.json` and Devtool's sanitized `--json` output.

A useful result is often **"G4 blocked: bundletool not installed"**, **"G2 passed on phone: refresh works"**, or **"G5 failed: Windows Rust cell"**. Fix the one real problem, then continue your usual work. You don't need to perform all cases in a day.

## Current snapshot's boundaries (2026-10-13)

G0–G8 source anchors are consistent and the Actions test modules pass in this audit environment. Previous G8 caveat about two artifact tests was reproducible; the accompanying hotfix repairs one real runtime metadata-drift bug and modernizes one legacy test that incorrectly expected checksum bypass. None of this establishes a new GitHub, Android, Windows, GitLab, runner or broad V105 live gate. The paid private GitHub branch-protection rule is excluded by your decision. Formal release qualification remains false.
