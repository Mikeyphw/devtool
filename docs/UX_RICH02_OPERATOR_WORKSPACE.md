# UX-RICH02 · Shared native operator presentation

This consolidated milestone integrates Devtool's **existing** delivery, GitHub
Actions, validation, history, SSH, overlay and evidence authorities into one
**presentation-only** Rich operator view. It does not replace a state machine,
CLI schema, transaction engine, hosted CI policy, GitHub client, execution event
bus, validation phase scheduler, or remote SSH stream. No additional monitoring
or background polling is created.

## Integration matrix

| Domain | Existing native command path | Terminal behavior |
|---|---|---|
| Delivery sessions | `delivery session status/report/resume` | Native durable receipt and recovery state in Rich |
| GitHub Actions explorer | `actions recent/detail/ui/runs/view/jobs/overview/lineage` | Rich workflow/run/job discovery; `actions ui` adds inline read-only keyboard browsing |
| Actions pipeline | `actions pipeline status/plan` | Ordered, native journal node projection; no fake live polling |
| Validation sessions | `validation status/plan/events/report` | Native phase/session/event snapshots; other controls unchanged |
| Local history/log listing | `runs`, `logs list` | Bounded Rich records and existing log selections; raw `logs tail/follow` unaffected |
| SSH | `ssh sessions/runs/workspace/doctor/env` | Rich native worker/session snapshot; live stdout/stderr and SSH mutation unchanged |
| Overlay recovery | `overlay recovery-plan/plan/check/status` | Source/equivalence/conflict snapshot; `--diff` prints full unchanged diff |
| Workflow retry/campaign | `workflow retry`, campaign status/checkpoint | Render actual plan/results; native explicit approval still required |
| Evidence and qualification | `evidence`, `artifacts`, `delivery plan`, `actions qualification`, `actions release readiness` | Qualification facts; no success or release inference |

## Interaction and output modes

An actual local interactive TTY enables Rich automatically. `actions ui`
provides a foreground `j/k` selection, `Enter` detail, `v` verbosity, `r`
explicit native read-only refresh and `q` detach. It uses Rich inline, **no
alternate screen, Textual, clipboard, or background network polling**.

One-shot commands render readable snapshots and exit. They do not become a
new live watcher. Existing native `actions watch` and `delivery session ci`
continue to own bounded polling and observed job/step transitions. Existing
native progress/HUD remains the authority for actual build and test execution.

JSON/`--machine`/redirected output, no-color/`TERM=dumb`, and existing plain
host flags retain their documented semantics. `--verbose` remains available
for full diagnostics. SSH follow and logs follow keep genuine stdout/stderr.
`NO_COLOR`, `CLICOLOR=0`, `TERM=dumb` disable Rich; never force ANSI through
an output pipe. Machine JSON is not intercepted or prettified.

Devtool's existing `ui_core` and `rich_progress.py` remain intact; this
module consumes structured native dictionaries rather than creating a second
normalization/event source. `operator_rich.py` is read-only.

## Security and regression requirements

- Rendering cannot invoke native mutation operations, create session receipts,
  change completion states, or declare release qualification.
- Existing apply/commit/session/CI source verification is untouched.
- Source identity, missing/ambiguous checks, failures and skipped states remain
  exactly as reported by native engines.
- Text is bounded, sanitized and displayed without markup interpretation.
- No auto clipboard actions; remote log download remains explicit (`l` in the
  existing run monitor), with existing source/attempt/size/security protections.
- Read-only refresh in Actions UI uses the same `actions ui` data producer and
  only when explicitly requested by a key. No duplicate polling engines.
- Existing useful Textual implementations are not deleted or bypassed for
  non-launcher use; only the already configured plain native hosts are disabled.
- No obsolete gate is removed; the launcher CI job discovers the new tests.

## Acceptance limits

Local standalone Rich rendering tests validate the pure projection. Mandatory
staged functional suites and native overlay validation must run in the actual
Termux checkout at the exact source SHA. Actual PR HEAD/hosted required checks
must be verified after native commit. Device and formal release acceptance are
separate and **not** inferred from CLI snapshots.

Some domains are deliberately static native snapshots (e.g., `ssh sessions`,
`validation status`, and `actions pipeline status`); this is not advertised as
live monitoring. Persistent operator workspaces across commands and streaming
worker logs are future work, not silently simulated.

## CI registrar conditional workflow registration fix (consolidated)

`workflow_expectations()` still labels uncertain `pull_request.paths`,
`paths-ignore` and `types` trigger eligibility as `indeterminate`. Static
eligibility alone never constitutes proof, and an absent run stays blocked.
The native `select_registration()` now accepts **only** an independently
correlated run for such a required workflow (exact provider workflow ID,
requested event, bounded registration window, current PR source identity,
and exact attempt). Once observed, that workflow no longer appears as
`indeterminate`. Failed/incomplete jobs and concurrent ambiguous runs remain
blocked, and source-bound `verify_exact_attempts()` and branch protection
validation are still mandatory. No workflows are marked optional or skipped.

The existing registrar test is corrected to assert both no-run refusal and
registered-run acceptance. Additional regressions cover PR trigger types,
path filters, stale/wrong PR/branch/source event, wrong workflow ID, ambiguities
and failing runs. The native CI workflow already runs registrar pytest tests;
the local staged test gate now runs those updated pytest suites too.

## UX-RICH02 audit corrective consolidation (2026-11-16)

The earlier package's `actions ui` merely changed the selected-row marker and
`pipeline status` printed a flat list. This revision corrects both gaps.

- `actions ui` now has an in-memory, identity-preserving drilldown: `j/k` select,
  `Enter` open a child collection (`workflows → runs → attempts → jobs → steps`
  **only when such nested native data is actually available**), `h`/Backspace
  return to the parent, `v` toggle detail, `r` refresh local journals, `q`
  detach. The original native read-only Actions operator powers explicit `w`
  workflow discovery, `g` exact dispatch review, and `l` local lineage. These
  keys never dispatch, collect artifacts or retry runs. A run/job/step missing
  from an Actions source response is **not** synthesized or fetched implicitly.
- `actions pipeline plan/status` now visualizes each node's source-backed
  incoming `needs` edges. Newly created native pipeline journals retain
  manifest-validated `needs` lists; older schema-1 journals remain readable
  and the screen labels missing dependency metadata *unavailable*, not empty.
  No scheduler, authorization, dispatch, concurrency or resume decision changes.
- The launcher CI workflow now discovers dedicated dependency-projection tests
  as well as the operator tests. Terminal tests add nested navigation, DAG
  metadata and legacy-journal uncertainty coverage; registrar tests retain
  conditional registration accept/refuse/ambiguous/failure coverage.

### Limits not passed off as delivered

A full cross-domain persistent switcher, background/live session refresh for
validation/SSH, historical log browsing filters, and release qualification by
this UI are **not implemented**. Existing native monitor, shell, JSON and
approval paths remain authoritative. Textual interactive features such as
explicit artifact collection remain available only through the original native
commands/hosts, not silently recreated in this read-only Rich browser.

The candidate is source-pinned and must still pass the target-worktree native
launcher `check`, source-bound apply, unit/regression tests, and hosted CI. A
successful isolated renderer test is not a native delivery or device pass.
