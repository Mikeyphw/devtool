# GA-01 — Unified scoped App authentication (source candidate)

**Baseline:** GA-00 r2, `44906036e4ef5f2f8532365e57cf071016b91e3d`, tree
`4136660461ff523a74809e4e36b10df921c5f906`.
**Branch:** `feature/ga01-unified-scoped-app-auth-20261122`; do not merge before
GA-00 PR #45 and independent governance merge gate. **Roadmap:** proposed GA-01,
not yet in the committed native M0–M9 roadmap. No GitHub App installation,
secret, ruleset or release mutation occurs in GA-01.

## Native production integration

- `ga01_scoped_auth.py`: closed-world operation admission for Actions,
  Delivery and Federation. One operation selects exactly one M4 capability;
  installation/repository numeric IDs are reverified against the issued token.
  Unknown operations and ambiguous provider identities refuse before mutation.
- `github_actions.py` and `actions_cli.py`: all canonical App-supported Actions
  commands choose repository-bound minimum scopes; `auto`, `env`, `gh` and `none`
  remain unchanged. Explicit App never falls back to PAT. Long-running watch
  uses M4's renewal provider instead of a static token. Actions release `setup`
  is refused under `--auth app` because its owner-only `gh secret` operations
  are not an App adapter; it must not silently use a different credential.
- `github_app.py`: M7 private literal-only installed configuration is now the
  default base-installation source; separate `HEAD_` settings remain required
  for fork ownership. Scoped GET, workflow POST, run control, artifact redirect
  and log redirect reject nonmatching repositories before a request. No token
  appears in a receipt, Git remote, argv, or printed exception.
- `delivery_qualification.py`: existing PR evidence check uses the scoped
  App's read-only Checks client when explicitly requested instead of the generic
  `repository_read` credential. Delivery's PR, push and CI journals stay the
  only authorities; Federation's source lock and M8's event journal are reused.
- M7 credential doctor, effective read probe, capability matrix and drift
  report remain the sole source of user-visible credential diagnosis; GA-01
  does not create a second doctor or authority.

## Security boundaries and open implementation

Only existing endpoint-specific GET/POST request paths and native write
journals are integrated in this GA-01 source candidate. No permission to
invent arbitrary HTTP PATCH/PUT is introduced. GA-01's future
operation-specific PATCH/PUT adapters are **PARTIAL** until a real managed
caller, durable intent and negative tests are available. Do not call the
entire overlay `SOURCE_COMPLETE` solely because the native regression suite
passes. GitHub App scopes are maximum possible permissions, **not** authorization
or evidence that writes actually succeed. Live cross-installation/fork hostile
writes against a disposable repository remain a separate acceptance requirement.

## Required gates

1. Native check/apply/commit/session, clean exact source and tree, one GA-01
   branch/PR and verified remote head (nothing is dispatched by this document).
2. Independent exact-head six required hosted workflows, then one separately
   dispatched and qualified CI24 run with all 24 shards and summary artifacts.
3. App effective-read probe for exact repository + distinct fork installation
   where applicable; explicit authorized disposable-repo deny tests for wrong
   repo, expired grants, hostile remote push, and unknown network outcomes.
4. Shared governance ledger P09 protected signing at signing time, P15 merge
   policy at merge time, P16 security tests before security acceptance, P17
   release/device only at their actual enforcement boundaries. Do not repeat
   successful GA-00 CI24 for a new GA-01 commit.

## Backward compatibility

M4 installation tokens and M7 private `env.sh` remain supported, with no new
private-key persistence and no generic GitHub credential helper. Existing
native Delivery/PR/Actions/Federation/PAT `auto` paths are maintained. Source
validation is run by `python3 -u -B tests/ga01_native_validate.py` from the
candidate worktree; local tests are not hosted acceptance or a release.
