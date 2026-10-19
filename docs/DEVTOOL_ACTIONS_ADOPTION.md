# ACTIONS-A03: GitHub Actions repository adoption

ACTIONS-A03 is a **generator for reviewable consumer repository adoption**, not a second CI scheduler or release publisher.

- Existing `.devtool.toml` target-local jobs and target workflow references are parsed through Devtool's canonical configuration loader and `target_local_adoption_report`.
- `actions adopt` with no `--yes` is read-only and reports the proposed workflow filename and job count.
- `actions adopt --yes` requires a clean consumer Git worktree, an executable `devtoolw`, a named workflow with at least one target-local job, and the **full lowercase Git SHA** of a Devtool commit available remotely. It writes one new `.github/workflows/devtool-<target>-<workflow>.yml` and appends one `[wrapper.commands.ci-...]` stanza. Existing workflows/aliases are never replaced. No remote calls are made.
- `actions adopt --verify` verifies the generated workflow's exact bytes and wrapper alias presence; it is read-only.
- Generated GitHub workflows use `workflow_dispatch` only, minimal `contents: read` permissions, pinned checkout/upload actions, a detached frozen Devtool source checkout, source identity checks, the existing `plan`, `preflight`, `invoke` pipeline, and an evidence-only artifact whose contents include the consumer and Devtool source IDs and checksums.
- This is an **Ubuntu runner** template. An Android/Go/desktop target requiring additional SDKs/runners needs a repository-specific platform overlay before CI qualification. The generated workflow does not claim such qualification.
- Source SHA pinning does not independently authenticate the remote Git service; use organization-controlled source and required branch/review protections. The source SHA must be pushed before GitHub Actions can check it out.

Review:

```sh
python3 -m devtool --repo "$PWD" actions adopt --target app --workflow ci --devtool-sha FULL_40_CHAR_SHA
```

Write after review:

```sh
python3 -m devtool --repo "$PWD" actions adopt --target app --workflow ci --devtool-sha FULL_40_CHAR_SHA --yes
```

Verify:

```sh
python3 -m devtool --repo "$PWD" actions adopt --target app --workflow ci --devtool-sha FULL_40_CHAR_SHA --verify
```

The wrapper alias then runs the native DAG locally as `./devtoolw ci-app-ci`. The workflow dispatch is a separate, explicitly authorized GitHub action; this generator never dispatches one. Artifacts from remote workflows must still pass ACTIONS-A02's exact-run review/collect checks before use. Neither a successful adoption nor CI status constitutes formal security or release qualification.
