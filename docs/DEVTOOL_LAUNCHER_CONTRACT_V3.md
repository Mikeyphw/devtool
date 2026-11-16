# Devtool Python/Rich Launcher Contract v3

**Source implementation (non-release).** Devtool is the sole authority for CLI semantics, overlay validation, transactions/rollback, DAG planning, capability and host eligibility, durable delivery sessions, required CI, artifacts and recovery. The launcher is only an exact-source bootstrap, stage/approval coordinator, and result renderer.

## Architecture

`python3 -B -m devtool.launcher_contract_v3 build ...` is the **canonical builder**. Generated `.pyz` files include only a small `__main__.py`, one immutable `manifest.json`, and the source-pinned payload. They do **not** bundle another Devtool, GitHub client, scheduler, overlay engine or copied launcher runtime. Each package binds the target origin/branch/HEAD/tree, every committed modify-preimage blob, payload SHA256, and **Devtool's full HEAD/tree, native CLI schema blob and shared v3 module blob**.

Before any module import, the bootstrap checks Devtool's exact pinned HEAD and tree with Git replacement objects disabled. The v3 module then verifies selected worktree/origin/cleanliness, committed schema and module blobs, imported module path and the native CLI contract. The target and runtime worktrees should be **different**; use a clean, immutable Devtool runtime checkout (`~/Code/devtool`) to deliver a feature into `~/Code/devtool-v3-feature`. Never use stale source pins after a commit.

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

## Operate a generated package

```zsh
pkg="$HOME/storage/downloads/FEATURE_V3.pyz"
feature="$HOME/Code/target-feature"
runtime="$HOME/Code/devtool"
python3 -B "$pkg" inspect
python3 -B "$pkg" check --repo "$feature" --devtool-repo "$runtime"
python3 -B "$pkg" capabilities --repo "$feature" --devtool-repo "$runtime"
python3 -B "$pkg" plan --repo "$feature" --devtool-repo "$runtime" --target go --workflow validate
python3 -B "$pkg" apply --repo "$feature" --devtool-repo "$runtime"
```

`apply` writes only the local Git feature branch and creates a Devtool delivery session. Capture `LAUNCHER_V3_SESSION_ID` from the output; **do not reapply** after commit. Continue only with the saved session ID:

```zsh
sid=EXACT_32_LOWERCASE_HEX_SESSION_ID
python3 -B "$pkg" status --repo "$feature" --devtool-repo "$runtime" --session-id "$sid"
python3 -B "$pkg" resume --repo "$feature" --devtool-repo "$runtime" --session-id "$sid"
python3 -B "$pkg" diagnose --repo "$feature" --devtool-repo "$runtime"
python3 -B "$pkg" report --repo "$feature" --devtool-repo "$runtime" --session-id "$sid"
# Only after explicit review and approval:
python3 -B "$pkg" deliver --repo "$feature" --devtool-repo "$runtime" --session-id "$sid" --approve-remote
python3 -B "$pkg" ci --repo "$feature" --devtool-repo "$runtime" --session-id "$sid" --checks 25 --interval 8
```

No local Android Gradle, hidden Git ref rewriting, force push, auto-merge, CI redispatch, signing, root operations or device actions. Missing evidence stays `UNKNOWN`/`PENDING`, never auto-promoted. Stored sessions remain bound to original commit, worktree and repository. Native errors are displayed with original stage and bounded redaction; live native output is forwarded, not replaced with synthetic text.

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
private state directory). A missing pinned object is fetched only from
`origin/main` and accepted only when the remote HEAD equals the pinned commit.
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
