# QW01 — native Devtool qualification wrapper workflows

These six commands are **canonical Devtool repository wrappers** backed by `targets.devtool.workflows` and `[[test]]` specs in `.devtool.toml`. The commands delegate to ACTIONS-02 durable dispatches, ACTIONS-03 run observation, ACTIONS-04 artifact verification, and the existing G3R v3 producer and independent verifier. They are not a new background scheduler, a replacement for GitHub's 24 runners, or a formal release qualification.

Run from the repo root in native Termux:

```bash
cd ~/Code/devtool
./devtoolw ci24-start
./devtoolw ci24-resume
./devtoolw g3r-native
./devtoolw g3r-verify
./devtoolw qualify-status
./devtoolw qualify-seal
```

`ci24-start` **sends exactly one GitHub workflow_dispatch** for your clean current Git branch/commit after ACTIONS-02 preflight and persists a local non-idempotent dispatch receipt **before** any HTTP POST. The record is in `$XDG_STATE_HOME/devtool/qualification-workflows/<repo-key>/`, or under `~/.local/state` if XDG_STATE_HOME is unset. Re-running start for the same commit is **blocked** (including uncertain dispatch outcomes). GitHub token resolution uses the existing `GH_TOKEN`/`GITHUB_TOKEN`/`gh auth token` sources; no credentials are written to reports.

`ci24-resume` is **read-only remote observation** plus verified local acquisition of the summary artifact, never a second dispatch. The GitHub API can accept a dispatch with HTTP 204 and no correlated run ID. **Never guess the run ID**. Inspect GitHub Actions and choose a concrete run yourself:

```bash
DEVTOOL_CI24_RUN_ID=38808020336 ./devtoolw ci24-resume
```

That number is illustrative: choose a run for the **current** HEAD, *not* a past candidate. A chosen run is explicitly marked uncorrelated and must match repository, workflow ID, `workflow_dispatch` event, source SHA, branch, completed success, all **24 distinct** successful matrix jobs, the successful fail-closed summarizer, and exactly 25 correctly named/signed-checksummed artifact metadata records (including one summary). Devtool acquires and SHA256-verifies the summary archive with ACTIONS-04, then validates `summary.json` for exact SHA, 24 shards, complete module coverage, zero errors/issues/failures, and a non-release scope. To avoid ambiguous artifact provenance, **GitHub reruns (`run_attempt > 1`) are blocked by QW01** until an attempt-bound evidence contract exists. Status while GitHub is still running is not a pass.

`g3r-native` requires native Termux, clean Git, full **10 semantic + 3 native** lanes, and produces evidence outside the repository, followed immediately by independent source-bound verification. It may run for a long time on slower devices; it is foreground work and must not be treated as a daemon. If evidence already exists, avoid repeating it; use `g3r-verify` instead. An explicit external candidate can be supplied without copying it into the source tree:

```bash
DEVTOOL_G3R_CANDIDATE="$HOME/Downloads/candidate-v3-NEW_SHA.zip" ./devtoolw g3r-verify
```

`qualify-status` shows **saved last-observed** receipts; its machine JSON always declares `automated_milestone_verified: false`. It cannot turn a saved record into fresh qualification. `qualify-seal` independently re-reads the remote CI job/artifact evidence, re-verifies the 13 native lanes against current HEAD, and runs `devtool final-seal --strict`. Only after these checks does it report `PASS_AUTOMATED_MILESTONE_ONLY`, keeping `formal_release_qualified: false` and reporting the six carried real-substrate obligations (ENV-A06, ANDROID-A16, DIST-A01, DIST-A12, DIST-A13, TEST-A11). No publication or release action is performed.

All source mutations produce a new commit and invalidate previous CI24/native evidence automatically. The old evidence is preserved as historical provenance and is not promoted to the new source.

Troubleshooting: `ci24-start` must be invoked **once** for each commit. If GitHub response attribution is uncertain, inspect the existing ACTIONS-02 receipt and GitHub Actions UI; do not retry POST. For CLI-only operations and CI test harnesses, `python3 -m devtool.qualification_workflows <command> --repo <path>` uses the same authority and accepts `--run-id`/`--candidate` for relevant modes. Jobs are handled synchronously by Devtool's existing runner; any missing credentials/network/device capability blocks with nonzero exit status.

## QW01R2: in-process strict FS03 audit

`qualify-seal` uses `devtool.final_seal.final_seal_report` directly in the already-running
Devtool workflow process. It **no longer launches** a second Devtool CLI in a child
process. On native Termux, the QW01R1 nested subprocess could stall for 600 seconds
without output. The same 16 canonical FS03 checks now execute in-process, and the
wrapper additionally requires correct check identity, unique IDs, required inventory,
all-PASS statuses, internally consistent counts, source root, canonical gate scope,
and `formal_release_qualified: false`. Exceptions and incomplete reports fail closed.

The failure code previously labeled `network_timeout` was a generic diagnostic for
a subprocess timeout; it does **not** establish a GitHub outage. Existing CI24 and
G3R receipts remain usable for the **same** commit but must be reverified by the
seal. Applying this repair changes the source commit, so do **not** reuse evidence
from an earlier commit; fresh CI24 and native G3R qualifications are required.

## QW02: phase timing, pending observations and pre-dispatch authority

`qualify-seal` still executes CI24, G3R native evidence, and FS03 independently. It now emits flushed stage start/completion/blocked progress on stderr, and successful machine output includes `stage_durations_seconds` (`ci24`, `g3r`, `fs03`, `total`). **This instruments** the previously observed 850-second Termux delay; it does not yet prove which phase causes it or eliminate verification work.

`ci24-resume` observes an unfinished GitHub run with process exit 0 because the observation itself succeeded, while returning explicit `state=WAITING_REMOTE`, `observation_completed=true`, `qualification_complete=false`, `ci_verified=false`. A returned process exit zero is **not** qualification evidence; the final seal still blocks if CI is pending. Failed, tampered, and mismatched CI evidence retain nonzero errors.

`ci24-start` performs read-only remote SHA/workflow preflight before creating a dispatch receipt. The underlying ACTIONS-02 dispatch rechecks `expected_source_sha` immediately before any POST or dispatch journal. A definitive `GitHubDispatchPreflightError` retains a recoverable `PREFLIGHT_REJECTED` receipt, with source and prior failure history; a transport/POST-ambiguous outcome retains `DISPATCH_OUTCOME_UNKNOWN` and blocks redispatch. There is **no** automatic uncertain-run correlation or repeated dispatch.

After applying QW02 and committing a new source revision, perform **one** CI24 + native G3R current-source qualification sequence and the strict seal. Formal release is still not qualified until the six carried real-substrate obligations are closed.

## QW03: bounded live GitHub observation and FS03 sub-check timings

`qualify-seal` continues to verify the current GitHub CI24 execution, the current
native G3R candidate, and the 16 canonical FS03 contracts. QW03 reduces redundant
GitHub reads in the CI24 revalidation path: artifact inventory, artifact download,
and registry verification reuse one independently fetched remote run **within that
single operation**. The verifier makes a second real GitHub run request *after*
all archive checks and fails closed if source, workflow, run attempt, completion,
or conclusion changed. Nothing is cached between commands or across source SHAs.

Progress on stderr now includes `ci24.remote-run`, `ci24.jobs-fetch`,
`ci24.artifact-list`, `ci24.summary-archive`, `ci24.final-remote` and one
`fs03.<check-id>` timing per canonical check, in addition to QW02 stage durations.
No qualification proof is produced by timing/log output itself. A callback error
blocks FS03. This is a concrete redundant-I/O repair **and** a diagnostic for the
previous 850-second device run; it is **not** a proven end-to-end native speedup
until the next real Termux seal reports timings. No network operation, digest
validation, source manifest traversal or required FS03 check was removed.
