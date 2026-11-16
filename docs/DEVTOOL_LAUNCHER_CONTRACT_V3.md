# Devtool Python/Rich Launcher Contract v3.1

**Source implementation (non-release).** Devtool is the sole authority for CLI semantics, overlay validation, transactions/rollback, DAG planning, capability and host eligibility, durable delivery sessions, required CI, artifacts and recovery. The launcher is only an exact-source bootstrap, stage/approval coordinator, and result renderer.

## Architecture

`python3 -B -m devtool.launcher_contract_v3 build ...` is the **canonical builder**. Generated `.pyz` files include only a small `__main__.py`, one immutable `manifest.json`, and the source-pinned payload. They do **not** bundle another Devtool, GitHub client, scheduler, overlay engine or copied launcher runtime. Each package binds the target origin/branch/HEAD/tree, every committed modify-preimage blob, payload SHA256, and **Devtool's full HEAD/tree, native CLI schema blob and shared v3 module blob**.

Before any module import, the bootstrap checks Devtool's exact pinned HEAD and tree with Git replacement objects disabled. The v3 module then verifies selected worktree/origin/cleanliness, committed schema and module blobs, imported module path and the native CLI contract. The target and runtime execution worktrees are separated by the exact-runtime resolver before any native overlay mutation. A caller can run the launcher inside its target worktree, including a Devtool worktree; the runtime is resolved to a stable exact pinned checkout or detached cache. Never silently repin an existing immutable package after its commit.

All mutation uses the native `delivery adoption scaffold` -> `validate overlay` -> `apply overlay --yes --commit ... --static-validate --keep-artifact` transaction, followed by a fresh source-bound `delivery session start`. `check` itself runs the **native scaffold plus validator** but does not apply the overlay, write Git refs or contact GitHub. Native RUN-V3/LIFE cleanup receipts are preserved and reported, not deleted or reinterpreted as release evidence. Remote push/PR must be requested with `--approve-remote`; CI uses the exact committed Devtool policy and session, never guessed workflow names. A non-green CI report returns exit code 3 even when native observation completed successfully.

The optional native paths use *existing* Devtool `plan`, `capabilities`, `overlay recovery-plan`, `artifacts verify --receipt`, `delivery session resume`, `status`, and `report`, without another implementation of those features. Planning is read-only and does not execute a DAG. `diagnose` never automatically rolls back. `verify-artifact` verifies a specific receipt but does not establish signing, production authenticity, or formal release authorization.

## Build an immutable feature launcher

The feature checkout must be an existing, clean **named branch** with canonical GitHub `origin`, and `plan.json` must use Devtool S7 schema v1 (bounded `add`/`modify` entries) with a separate matching payload tree. This contract intentionally excludes delete; an authorized native delete overlay requires separate review/contract.

```zsh
python3 -B -m devtool.launcher_contract_v3 build \
  --repo "$HOME/Code/target-feature" \
  --devtool-repo "$HOME/Code/devtool" \
  --plan "$HOME/storage/downloads/plan.json" \
  --payload "$HOME/storage/downloads/feature-payload" \
  --output "$HOME/storage/downloads/FEATURE_V3.pyz" \
  --name feature-v3 \
  --commit-message 'feat: introduce reviewed feature' \
  --pr-title 'Introduce reviewed feature'
```

The output is exclusively created (`O_EXCL`) outside the target; it refuses overwrites, wrong source/remote, missing preimages, symlink traversal, unreviewed add/modify, and oversized/extra ZIP members. Archive SHA256 is an integrity check, **not a signature**.

## One-command operation (v3.1)

Run from anywhere **inside the exact target Git worktree**. No `--repo` or
`--devtool-repo` is required in the normal case. The ZIP bootstrap verifies
its target origin/branch from Git and finds a clean Devtool checkout at the
manifest's pinned SHA/tree/module; it may create a private detached runtime.
If autodiscovery cannot prove the origin or there is ambiguity, it refuses.
`DEVTOOL_REPO=~/Code/devtool` or explicit `--devtool-repo` is a supported override.

```zsh
cd "$HOME/Code/target-feature"
pkg="$HOME/storage/downloads/FEATURE_V3.pyz"
python3 -B "$pkg" apply --approve-remote --watch-ci
```

This **one invocation** performs exact preflight and native scaffold/validation,
atomic overlay commit, durable session creation, authorized native push/PR, and
bounded native required-CI observation. `--approve-remote` is **always explicit**:
without it, `apply` stays local. `--watch-ci` is optional and requires approval;
without it, the local apply/push still occur but CI must be checked later.
If required CI is still non-green, it returns a nonzero exit code and retains
the session; do not replay `apply` after a commit.

`inspect`, `check`, `status`, `resume`, `report`, `ci`, `diagnose`, `plan`,
`capabilities`, and `verify-artifact` remain native optional controls. E.g.:

```zsh
python3 -B "$pkg" inspect
python3 -B "$pkg" check
python3 -B "$pkg" apply  # explicitly local-only
python3 -B "$pkg" ci --session-id EXACT_32_CHAR_ID
```

Inside a wrong worktree the bootstrap refuses before importing Devtool.
Explicit `--repo PATH --devtool-repo PATH` remains for remote working
locations, troubleshooting, and machines without standard checkout paths.
Git fast-forward is still conditional on the exact manifest SHA and proof
of ancestry, never forced or rebased.

### Native, non-Textual and colored presentation

Every native Devtool child receives the supported Devtool switches:
`DEVTOOL_EXECUTION_PRESENTATION=plain`, `DEVTOOL_JOB_UI=plain`,
`DEVTOOL_TEXTUAL_APPLY_HOST=0`, and `DEVTOOL_TEXTUAL_EXECUTION_HOST=0`.
This **selects Devtool's own Rich-capable terminal mode**—the launcher does
not implement a Textual alternative or capture/rewrite live native output.
Launcher status lines use Rich styling on a real terminal; colors are permitted
in native child output only for a real TTY and not when `NO_COLOR`,
`CLICOLOR=0`, or `TERM=dumb` are explicit. Redirected/machine output stays plain.

The immutable bootstrap applies the same presentation environment to the
parent before source-bound module import. Existing v3.0 packages must be
**rebuilt from v3.1**; the old immutable bootstrap cannot silently upgrade.


## Git fetch and fast-forward automation (HF01)

The v3 launcher **automatically aligns source checkouts** without `git pull`,
without setting upstream tracking and without rebasing. If a target feature
checkout is behind the exact manifest HEAD, it fetches only its named remote
branch into `FETCH_HEAD` (and may update remote-tracking metadata), checks that the fetched commit exactly matches the
immutable manifest, checks that the current HEAD is an ancestor, then executes
`git merge --ff-only PINNED_SHA`. Dirty trees, divergent heads, a moved remote,
wrong branch or wrong origin refuse without changing local source. The old
feature branch is never fast-forwarded to `main`. The `--no-auto-ff` flag
disables automatic network fetch and local fast-forward.

For the **Devtool runtime**, it first reuses an existing clean worktree at
the pinned HEAD/tree; if the requested checkout is clean `main`, it may
fast-forward that checkout only to the exact pinned `origin/main`. If the
requested checkout is a separate older feature branch, it is left untouched.
The runtime is instead reused or provisioned as a **detached, exact-pinned
worktree** under `$XDG_STATE_HOME/devtool/launcher-runtimes` (or the standard
private state directory). A missing pinned object is fetched from `origin/main` only when the exact
pinned commit is proven to be an ancestor of the fetched main HEAD and its tree
matches the manifest; moving main never upgrades the launcher source pin.
This private runtime survives the feature commit and remains available to
`status`, `resume`, `deliver` and `ci`. The source checkout is never rewound
while observing those later stages. The operator sees what was fetched, moved
or created; native Git and Devtool remain responsible for transactions.

There is no automatic `git worktree prune`, reset, rebase, forced update, or
change to tracking configuration. A cached runtime that is dirty or no longer
pinned is a hard refusal, never silently cleaned or replaced.

## Acceptance and limitations

- Unit tests: all original v2 safety checks plus v3 runtime/zipapp pins, manifest-only delivery, native DAG/capability/recovery/artifact/resume delegation, non-green CI and full preflight validation.
- Disposable tests must exercise the **installed native Devtool** scaffold/validator/apply/commit and durable session; no `pytest` output is treated as external hosted/device evidence.
- The new source-owned GitHub workflow runs tests on the exact PR head. It does not dispatch other workflows, approve releases, or assert formal external qualification.
- Source-dependent launchers are intentionally *not portable between arbitrary Devtool versions*. After a reviewed Devtool update, rebuild a new package; do not silently repin old packages.
- Delivery module is a Python `-m` entrypoint in v3; `core_cli_schema.json` remains unchanged and native CLI subcommands retain their original owners. A future explicit CLI schema registration may be done with generated surface synchronization when that is beneficial.
