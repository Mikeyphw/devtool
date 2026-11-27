# W4 — consolidated native Rich acceptance (RICH-U-C01)

Exact base: merged PR55 `f5b02e22691c39dd9500de5b766659156336b6c6`, tree `4f034b4c04c2a674a5968d06f562463600a7a3d8`. No new original overlay IDs. W3 native, CI24 and selection evidence retained at original scope. W4 is eligible without inferring full W3 acceptance.

`inline_live.py` owns the only Rich Live constructor; `posix_input.py` owns the shared keyboard/terminal lease. Overlay, CI, saved-operation browser and operator workspace delegate to these modules. Source/attempt changes invalidate log data; no additional GitHub query occurs on selection or refresh. Native execution and its original status remain independent of display. Rich is the default read-only native workspace; Textual is explicitly selectable for its existing action surfaces. Machine one-shot output retains canonical snapshot schema.

Explicit log authorization is bound at keypress to the exact repository, source, workflow, run and attempt. Reordering retains the selected run; navigating, refreshing, closing or detaching cannot silently issue another GET. Closing or changing an in-flight selection discards its returned display data; it does not cancel a native download. The native download adapter uses `/runs/{id}/attempts/{attempt}/logs` and includes the attempt in its receipt and private archive name. Legacy explicit non-Rich `actions logs` retains its existing latest-attempt semantics. Local callback observation time is distinguished from native journal timestamps.

Native required validation: `python3 -B -u tests/w4_native_validate.py`. W3 full hosted inventory stays unchanged; W4 hosted complement contains only new/nonoverlapping modules. CI24 remains independent and exhaustive. Real physical Termux/SSH/tmux behavior and authenticated provider observations cannot be inferred from local PTY fixtures.

| Original ID | Requirement | Source/fixture scope and remaining evidence |
|---|---|---|
| M6-01 | Overlay and CI share one Rich Live host | Shared host/input and PTY regressions; actual Termux evidence pending. |
| M6-02 | Operator workspace read-only view shares same terminal owner | Shared host/input and PTY regressions; actual Termux evidence pending. |
| M6-03 | Overlay presentation consumes canonical native apply/events | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-04 | No duplicate GitHub/CI/apply engine | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-05 | One column even at wide widths | Shared host/input and PTY regressions; actual Termux evidence pending. |
| M6-06 | Compact repo/branch/source/runtime/artifact identity | Shared host/input and PTY regressions; actual Termux evidence pending. |
| M6-07 | Ordered actual stages and source-derived counters | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-08 | Timestamped real events and bounded viewport | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-09 | Original native stdout/stderr remain visible | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-10 | Durable logs separate from bounded display | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-11 | Determinate progress only with real completed/total | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-12 | No fabricated required CI or release success | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-13 | CI observer uses same shared Rich, not second dashboard | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-14 | Exact PR HEAD/workflow/run/attempt/job/step preserved | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-15 | Keyboard j/k/arrows/Enter/v retained | Shared host/input and PTY regressions; actual Termux evidence pending. |
| M6-16 | Explicit source-bound log GET and [`[`/`]`] paging | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-17 | Manual refresh preserves CI polling budget | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-18 | Detach never cancels/reruns/falsely qualifies | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-19 | App identity, installation/capability status visible | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-20 | Auth, operation approval, remote verification, CI are distinct | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-21 | Fork/base access, expiry, drift visible | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-22 | Approval guidance, never implicit install or permission increase | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-23 | Nerd/Unicode/ASCII icons with graceful fallback | Shared host/input and PTY regressions; actual Termux evidence pending. |
| M6-24 | Unicode/emoji/grapheme display width safe | Shared host/input and PTY regressions; actual Termux evidence pending. |
| M6-25 | Status discernible without color | Shared host/input and PTY regressions; actual Termux evidence pending. |
| M6-26 | Inline `screen=False`, `transient=False`, no alt-screen | Shared host/input and PTY regressions; actual Termux evidence pending. |
| M6-27 | Terminal restored on exception/SIGINT/resize | Shared host/input and PTY regressions; actual Termux evidence pending. |
| M6-28 | Plain/non-TTY/JSON/CI behavior remains faithful | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-29 | No untrusted ANSI/markup or credential leakage | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-30 | Clipboard only on explicit collection/copy | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-31 | Test updates retain security and current-behavior invariants | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-32 | Termux portrait 40/60/80, SSH/tmux and resize | Shared host/input and PTY regressions; actual Termux evidence pending. |
| M6-33 | ZIP/PYZ native check/apply/commit/session plus CI | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-34 | Rich primary default, Textual explicitly selectable | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| M6-35 | Canonical docs/CLI/schema/workflow filters up to date | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| U01-P01 | Extract shared inline Rich Live host | Shared host/input and PTY regressions; actual Termux evidence pending. |
| U01-P02 | OverlayLiveProjection uses shared host | Shared host/input and PTY regressions; actual Termux evidence pending. |
| U01-P03 | Always-vertical 28–120-column layout | Shared host/input and PTY regressions; actual Termux evidence pending. |
| U01-P04 | Remove width floor; live geometry/resize | Shared host/input and PTY regressions; actual Termux evidence pending. |
| U01-P05 | Focus/log paging survives all widths | Shared host/input and PTY regressions; actual Termux evidence pending. |
| U01-P06 | Keep Textual selectable | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| U01-P07 | Presentation cannot mutate verdict/clipboard | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| U01-P08 | Source changes through native apply/commit/session | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| U01-P09 | Full integration, hosted, Termux/device tests | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| U02-P01 | Delivery monitor uses shared Live host | Shared host/input and PTY regressions; actual Termux evidence pending. |
| U02-P02 | POSIX keyboard extracted to shared input module | Shared host/input and PTY regressions; actual Termux evidence pending. |
| U02-P03 | Preserve all CI keyboard controls | Shared host/input and PTY regressions; actual Termux evidence pending. |
| U02-P04 | Explicit source-bound logs/pagination | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| U02-P05 | CI poll/detach/verdict remain native-owned | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| U02-P06 | Restore TTY on all failure/signal/resize paths | Shared host/input and PTY regressions; actual Termux evidence pending. |
| U02-P07 | Exact-source preflight and idempotent retry | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| U02-P08 | Atomic operation/recovery under Devtool lifecycle | Native source and isolated regression; actual source-bound host/remote evidence pending. |
| U02-P09 | Focused tests and real PTY evidence | Shared host/input and PTY regressions; actual Termux evidence pending. |
| U02-P10 | Preserve Textual/JSON/plain/clipboard/release boundaries | Native source and isolated regression; actual source-bound host/remote evidence pending. |

## RICH-U-C01-F04 — physical workspace repair

The workspace budgets every frame against the live terminal width and height, keeps the selected record and a sticky detach/help footer in view, and pages details and individual job logs by original source-line/cluster offset. `j/k` or arrows select; Enter opens cached steps/details; `w` discovers workflows, `g` explicitly reads selected workflow runs or exact run-attempt jobs, and `l` reads selected job logs; `b` returns without another GET, `[/]` pages, `?` opens help. Start the Actions route with explicit `--auth app` for the repository-bound `actions_read` scope; no fallback to PAT/gh on App failure. Provider failures retain the current source and show only exception class. The mixed timestamp catalog reader orders existing epochs and aware ISO times without rewriting receipts. Native job logs are retained under private state, digest verified and rendered after redaction. Physical Termux acceptance, live provider reads, native apply, and updated exact-head CI remain separate gates. The prior PR56 head must be continued by fast-forward only; no replay or new PR.

### F04-HF02: bounded native validation and CI evidence

For the exact cumulative reviewed overlay, the owner-side native validator selects only the explicit bounded `NATIVE_MODULES` inventory. Unknown/extra/missing changed paths and missing mandatory checks fail closed. No runtime flag disables source identity, preflight, transaction, rollback or selected regression checks. A completed bounded native gate is not exhaustive acceptance. The explicit `--full-local` diagnostic option is available, but CI24 delivery never invokes it. The W3 hosted full inventory, W4 hosted complement and CI24 24-shard repository inventory remain intact.

CI24 PR/feature qualification remains exact-commit. A later canonical `main` commit can reuse an original complete CI24 result only if the tested commit is an ancestor, the whole Git tree is identical, the saved locator is unambiguous and the original run, jobs and all 25 archive bytes pass fresh native verification. Reuse is reported as `PASS_CI24_TREE_REUSED`, not exact-commit or release acceptance. Missing/expired/invalid historical evidence requires fresh qualification; source drift refuses dispatch.

Six source-only workflows still run their tests for PR and manual events. Post-merge main pushes can reuse original authenticated executed PR evidence only for the actual merged PR, identical full tree, exact workflow and runner scope, successful named test step and original-attempt artifact digest. Otherwise they run their original tests. No hosted consumer, device, release or native transaction gate is deduplicated.

### F04-HF03: kernel-bounded executable fixture paths

The owner HF02 run on 2026-11-27 reached module 6/14 and failed both Bash and sh direct-execution fixtures with `ENOEXEC` under the nested native runner temporary root; native rollback was reported successful. A shell script interpreter pathname is subject to the kernel shebang buffer independently of CPU architecture. The revised fixture locates its mock interpreter aliases in an existing short, private test directory and asserts a complete shebang of at most 127 bytes while executing the same original script body directly from a deliberately deep path. The wrapper forwarding fixture uses the same bound. No production interpreter, validation selection, CI24 requirement, or existing acceptance obligation is weakened. The exact HF02 history and archive are retained, and any successor requires independent verification of all predecessor rollback receipts before mutation. Physical Termux and hosted acceptance remain pending.


### F04-HF04: hosted dependency parity

GA run 46026921202 (job 140600598097) on HF03 head `08febf3a9d96477beea50a2f46b4ee3daf9c3218` failed RM-08 coverage execution and skipped twelve Textual cases because the GA install omitted those dependencies. GA now uses CI24's reviewed dependency ranges and editable Textual extra, with an explicit import gate before W3 and its W4 complement. Structured workflow tests check GA, CI24, RM15, RM17, RM21 (both jobs), and RM22 dependency availability/order; negative fixtures refuse absent, conditional, ignored-error, late and commented installs or missing/conditional import preflight. The same 14 native modules and exhaustive hosted inventories remain required. This is a same-PR subordinate repair; exact-head hosted GA, required CI, CI24 and device acceptance remain pending.


### M6 interactive run drill-down (RICH-U-C01-F04-HF05)

On exact HF04 head `854126dd622b485dde3dedd7e49d9f096ff6ba23`, GA Source Acceptance run 46047973864 and all fifteen observed PR-triggered workflows succeeded; CI24 run 46048410600 completed all 24 shards and its summary. Those results are predecessor evidence only.

In the live native PR checks monitor, `j/k` or arrow keys select a workflow run (the stages list is height-bounded); `Enter` opens its independently verified run-attempt jobs. `Enter` on a job opens its steps, and `Enter` on a step opens its recorded details. `l` inside a run's jobs or steps explicitly loads the selected job's native log; at the root it retains the existing run-log route. `v` opens full run/source/workflow and selected job identity, `[/]` page, `b`, Esc or Backspace returns without a provider read. `r` in the drill-down refreshes only that read-only view; outside it, the existing bounded CI refresh behavior is unchanged. `q` detaches without cancelling or rerunning jobs. A terminal native success is not held unless an operator has already explicitly opened a run; a failed native verdict retains its existing review behavior. JSON and plain modes do not create a navigation provider.

The existing `workspace_actions.run_jobs` and `job_logs` are the only drill-down read authority and use the requested auth scope. Selection is bound to the observed session, repository, PR source, workflow ID, exact run ID, attempt, provider run SHA and event (including a verified synthetic merge SHA distinct from the PR source). No request occurs on render or mere selection. A stale/missing/duplicate run or changed context invalidates queued and in-flight publication; provider identity mismatch or unavailable permission shows a refusal, never changes the native CI verdict. Jobs/steps require unique positive identities and exact run membership, and the native 200-job cap is disclosed. The existing shared viewport pages complete redacted job-log lines by source position; no alternate screen, background CI poller, mutation, extra clipboard access, or new command is introduced. All previous source, hosted, device and release requirements remain intact; M6 physical Termux navigation and exact-head successor CI remain pending.

The exact bounded owner-side native profile adds the existing `test_m6_live.py` module (15 modules total) to cover the new production route. W3, the hosted W4 complement and exhaustive CI24 remain unchanged in scope.
