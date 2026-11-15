# Devtool delivery sessions — current contract

`devtool delivery plan ARTIFACT` remains the original, authoritative LIFE artifact
qualification command. `devtool delivery session` provides a separate, resumable
source-bound GitHub delivery workflow. No duplicate scheduler, apply engine or
GitHub workflow-dispatch API is introduced.

## CLI

```sh
python3 -m devtool --repo "$HOME/Code/devtool" delivery session plan --base main
python3 -m devtool --repo "$HOME/Code/devtool" delivery session start --base main
python3 -m devtool --repo "$HOME/Code/devtool" delivery session push --id ID --yes
python3 -m devtool --repo "$HOME/Code/devtool" delivery session pr --id ID --yes --title 'Delivery improvements'
python3 -m devtool --repo "$HOME/Code/devtool" delivery session ci --id ID --checks 20 --interval 8
python3 -m devtool --repo "$HOME/Code/devtool" delivery session status --id ID
python3 -m devtool --repo "$HOME/Code/devtool" delivery session resume --id ID
python3 -m devtool --repo "$HOME/Code/devtool" delivery session report --id ID
```

The consolidated Python/Rich ZIP launcher is the preferred operator interface;
it extracts only its payload to `$TMPDIR`, runs Devtool with an explicitly selected
repository, and continues after the apply process has exited to create a session,
request separate authorization before GitHub writes, discover CI, and issue a
complete local report. This feature does not perform local Android Gradle builds.

## Source and mutation safety

- Source is bound to the checked-out branch, exact commit, Git worktree and remote
  identity. Missing/changed/ambiguous remote branches are not force-pushed.
- Remote push and GitHub PR creation require explicit `--yes`. A private intent
  is fsynced **before** each mutation; uncertain outcomes never retry.
- GitHub PR lookup happens before PR creation. An independently observed open PR
  is reused only if repository, branch, base and head commit match precisely.
  New PRs are drafts. PR creation responses are rechecked with a subsequent GET.
- A PR POST that may have succeeded, but cannot be independently verified,
  leaves a blocked intent. Use read-only reconciliation rather than a new POST.
- CI never dispatches workflows automatically. Existing Devtool `actions` owns
  explicit dispatch and its own correlated request receipts.
- PR/push provenance is not a release qualification. All remote mutation steps
  require an authenticated transport where appropriate.

## CI registration and evidence

- `ci` polls a bounded number of times and treats missing expected runs as
  `WAITING_REGISTRATION`, not failure. It uses the existing Devtool Actions client.
- Push runs must match the exact source SHA **and** branch. Explicit dispatches remain owned by the existing Actions receipt engine and are not inferred from the run listing.
  PR runs are evaluated only after independently revalidating the current open PR head and base. They match a current synthetic merge ref or an exact
  PR-to-head association; stale merge refs cannot qualify a newer commit.
- Required workflow names and stable workflow IDs are loaded from committed
  `config/delivery-required-ci.json`; manual `--require-workflow` inputs are checked
  against that policy, never used to replace it. Other runs remain observational.
- Failed required runs include exact attempt job/step names. An API outage never
  converts the previous remote observation into success. The last snapshot is
  stored in the existing private delivery-session journal.
- The first 100 recent workflows/runs are scanned per observation. A missing
  workflow beyond this bounded listing remains **unconfirmed**.
- If several different workflows share the same display name, qualification is
  blocked as ambiguous. This slice does not infer protected branch check sets.
- Formal signing, Android device acceptance and release qualification are
  separate operations; a passing workflow never implies release readiness.

## Current-state test policy

Run current tests for source binding, idempotence, uncertain push/PR outcomes,
registration delay, exact PR merge verification, failure diagnostics and canonical
CLI schemas. Update or remove obsolete assertions instead of carrying historical
milestone tests. Preserve immutable CI/audit evidence for provenance.

## S6 unified delivery evidence

`delivery session status --id ID --json` now projects one bounded event stream
from the S1 journal. `delivery session report --id ID` renders the historical
source-bound evidence in Rich (TTY) or text; `--json` exposes the same `receipt`
and `event_stream` for other frontends. Reports never refresh remote CI; see
`docs/DELIVERY_S6_EVIDENCE.md` for restart and release boundaries.
