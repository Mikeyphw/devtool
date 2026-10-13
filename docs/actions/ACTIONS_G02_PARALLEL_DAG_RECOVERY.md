# ACTIONS G02 — Bounded parallel remote DAG, conservative recovery, cancellation

**Source baseline:** user-provided Devtool at `e72335640aab0db58ae96c3e319a6bcec1e2ac19`, *after* admitted G00+G01 overlay. This is a source-targeted implementation, not evidence of a remote live CI acceptance. Original G0 obligation ledger remains frozen.

## Implemented scope

- Existing `actions pipeline plan/run/status/resume` model remains authoritative, with source+manifest binding; local `invoke` delegates to Devtool's existing executor and GitHub operations to the existing provider.
- A topologically eligible frontier schedules up to **four independent remote dispatch/result observations at a time**; no new remote executor is created. Local invocations, Git source checks and archive registry writes remain sequential. Ready independent work is not blocked by a waiting branch.
- Worker threads never mutate the shared pipeline journal; the owner records **every selected node as `running` before any HTTP POST**, then persists each outcome. A crashed dispatch stays `uncertain`—it is never repeated automatically.
- On explicit `resume`, interrupted `github-result` (read-only) and `local-git` may be inspected again. Interrupted dispatch, local mutation and artifact writes are **uncertain** pending human reconciliation. Completed nodes are never replayed.
- New `actions pipeline cancel --pipeline-id ID --yes` creates an owner-private, durable stop marker without GitHub authentication. Running foreground invocations observe it at scheduling boundaries; pending and waiting nodes become `cancelled`. **No remote GitHub job is cancelled and no arbitrary PID is killed.** Local work already executing may finish. Status is read-only and exposes `cancel_requested`.
- On cancelled `pipeline run/resume`, CLI exits nonzero; issuing the *stop request itself* is a success response. Projection recognizes `cancelled` as terminal; never treats it as release qualification.
- Source, signer, unsigned APK, release policy, external authentication and GitHub private branch protection are not changed.

## Targeted static acceptance

- Two concurrent dispatches require a thread barrier and each is recorded exactly once; independent observation branches both progress while waiting and the join fires only after each source-bound success.
- Seven independent requests never exceed the four-worker bound.
- Interrupted side effects are held as `uncertain` and no replay is allowed; read-only observations safely resume.
- Cancellation before, during and after processing, marker symlinks, no-op on completed pipelines and CLI confirmation/route are tested.
- Full ACTIONS regression + artifact producer/consumer suite required. On Termux Textual is compulsory; offline Linux Textual skips remain explicitly unqualified.

## Still OPEN — not promoted to full G2 closure

1. Actual GitHub live parallel/restart/cancel/retry evidence on Termux has not been collected (requested deferral of live negative matrix).
2. Native execution RunStore correlation across externally spawned `local-invoke` children and pipeline journal still needs explicit cross-runtime reconciliation. The journal is a scoped coordinator, **not** a second privileged executor.
3. Mid-execution OS cancellation of local tasks and remote jobs is deliberately unsupported: request-stop is cooperative at scheduling boundaries, not signal/kill semantics.
4. Cross-process crash-at-every-journal-write and multiple-device shared cache concurrency are not yet live-qualified.
5. CLI/GTK/HUD cross-surface cancellation presentation belongs to G3; this overlay changes only canonical CLI and side-effect-free projection.
6. Historical V105 full-campaign gates remain separately open; G1 live negatives remain deferred.

The full release remains `qualified_release=false`; successful ACTIONS-10 local APK signing is unaffected.
