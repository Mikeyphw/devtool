# UX-RICH04 · Canonical event explorer (Rich inline)

UX-RICH04 extends the source-bound UX-RICH03 foreground operator workspace.
Its new **5 Events** tab observes existing Devtool producers, without becoming
an event bus, scheduler, SSH transport, CI evaluator, or second history store.

## Native evidence inputs

- **RUN V3:** `RunCatalog.open(private_run_root(repo), create=False).events`.
  These records are **coalesced**. They carry a stable `event_id`, `run_id`,
  `kind`, and `update_count`, but **no canonical timestamp**. The interface
  does not invent time or merge them into a chronological sequence.
  Persisted catalog JSON sorts event identifiers, so UI and tests must address
  events by native producer/record identity, never positional list index.
- **Validation:** `load_active_session(repo, required=False)` plus
  `read_events(session, limit=N)`. These are **append-only** events carrying
  recorded `event_id`, `session_id`, revision, type, state, and timestamp.
  The reader is the pre-existing Devtool validation authority.
- **Actions/SSH:** unchanged, separate UX-RICH03 tabs. UX-RICH04 does **not**
  fetch remote Actions/SSH data under the guise of local events, invent
  remote job-step events, or automatically send network GETs.

Each refresh returns a bounded read-only native projection with explicit
producer metadata. Both sources have independent quotas, and a `no-records`
observation is never represented as execution success or release qualification.
Snapshots reside only in memory. The only persistent preference remains the
pre-existing source-bound selected tab outside the target repository.

## Terminal experience

```text
DEVTOOL · UX-RICH04   1 ACTIONS · 2 RUNS · 3 VALIDATION · 4 SSH · [5 EVENTS]
Source SHA… · AUTO · SSH MANUAL · read-only
NATIVE EVENTS · N visible / N observed
Run events: coalesced, unordered · Validation: append-only, timestamped
Source [all]  Status [all]  Search []
  event kind          state       source
› phase-end           success     validation
  process-start       failure     runs
```

Keys: `1`–`5` select tabs; `j/k` navigate; Enter toggles allowlisted native
identity/detail; `f` cycles source (`all/validation/runs`); `s` cycles status
(`all/failure/active/terminal`); `/` enters literal search on **displayed
labels**; Enter commits; Escape cancels, Backspace deletes; `c` clears
filters; `r` explicitly refreshes; `a` pauses/resumes local automatic
refreshes; `q` detaches. Typing `q` while editing a search is text, not exit.

The Events source is refreshed asynchronously by one bounded, single-flight
reader at a minimum 2-second interval. A slow read never blocks local key
handling and cannot queue another read behind it. The interval is **not a
reader timeout**. A completed read is re-attested against the current HEAD;
source drift or read-only contract violations are prominently refused.
`NO_COLOR`, `CLICOLOR=0`, `TERM=dumb`, JSON, CI, redirected outputs and
noninteractive terminals retain previous plain-mode behavior.

## Privacy and safety

No argv, raw stdout/stderr, tokens, environment, private paths, unvalidated
provider URLs, or arbitrary event payloads are rendered. Only an explicit
allowlist of already-produced fields is projected; status vocabulary is
strict, text is bounded and sanitized, and event keys are qualified by source.
The UI makes no writes to Devtool receipts or Git, starts no native executions,
does not dispatch/collect, does not elevate qualifications, and never writes
to the clipboard. Source-bound Git and durable native v3.1 launcher remain
sole delivery/commit/CI authorities.

## Checks and acceptance

`tests/test_operator_workspace.py` is **updated**, not disabled, to recognize
five tabs and one more safe local reader. The existing 40 workspace negative
and regression tests remain. `tests/test_operator_event_feed.py` extends tests
for native producer contracts, filtering, redaction, isolated event IDs,
source drift, invalid state, and asynchronous read-only safety. The existing
v3 launcher CI workflow includes both suites and listens for all changed files.
All required existing CI remains enabled; no historical/obsolete check was
removed merely to pass a build.

Offline tests and package integrity are not proof of Termux apply or hosted
checks. Native launcher `check` must succeed before `apply`; after commit,
recovery uses native `status`/`report`/`ci`/`deliver` and **never reapply**.
A new PR HEAD is not considered qualified until exact hosted evidence exists.
