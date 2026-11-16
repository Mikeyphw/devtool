# Textual GitHub Actions workspace — R1 audited consolidated delivery

## Intent

One interactive GitHub Actions workspace inside the existing `devtool tui` Textual control center, not a new top-level TUI host. `devtool tui --page github` and the canonical `a` shortcut open the page. The source of truth is still Devtool's existing `github_actions`, `actions_runs`, and `actions_dispatch` authorities, with the same authentication, journal and non-idempotent POST rules.

The v3.1 **delivery launcher** remains **native/plain** and never starts a Textual apply or execution host. `DEVTOOL_EXECUTION_PRESENTATION=plain`, `DEVTOOL_JOB_UI=plain`, `DEVTOOL_TEXTUAL_APPLY_HOST=0`, and `DEVTOOL_TEXTUAL_EXECUTION_HOST=0` are preserved. Native Devtool commands keep the supported `--no-clipboard` behavior.

## Views and local behavior

- Browse active and disabled workflows, up to **1,000** entries as bounded by the canonical client, paginated locally in pages of 15. Selecting a workflow filters runs by exact numeric workflow ID.
- Browse the **100 most recent** runs, paginated locally in pages of 16; **older runs beyond 100 are not fetched** in R1, and the UI explicitly warns when the bounded snapshot may be incomplete.
- Search by workflow, run name, branch, SHA or ID. Re-render only changed keyed tables, retaining cursor positions where possible.
- Select an exact run and view its current attempt, complete 40-character head SHA, status/conclusion, event, up to 200 jobs of that attempt, and bounded per-job steps. No run-attempt mixing or inferred CI qualification.
- Explicit **Fetch logs** delegates to `actions_runs.remote_logs`; it revalidates run identity and attempt before and after download. ZIP entries are read only as bounded previews; nothing is extracted or placed on clipboard. The canonical private archive receipt is retained. Signed redirect URLs and authorization tokens are never displayed.
- **Cancel / Rerun** are explicitly confirmed and delegated to `actions_runs.control_run`, which journals the intent before its one remote POST. UI reports accepted-pending or uncertain outcomes, never confirmed completion.
- **Dispatch** requires an exact selected workflow, explicit ref and JSON inputs, plus a confirmation. The `actions_dispatch.dispatch_workflow` authority validates YAML `workflow_dispatch` schema, required inputs, remote source binding to clean local HEAD, duplicate/uncertain dispatch receipts and the sole POST. Existing ambiguous dispatches are never auto-replayed.
- Background remote read workers and periodic local-control-center projection polling avoid blocking the Textual event loop. Provider failure leaves the last known view marked **STALE**; explicit errors never masquerade as qualification.
- Responsive keyboard/viewport behavior at 42×16, 64×22, 80×24 and 120×35. Searchable Ctrl+P palette uses the canonical shortcut authority.

## Source and regression integration

- New `devtool/textual_github_workspace.py` read-side projections and journal-authority adapters; `devtool/textual_github_view.py` standalone Textual workspace factory and confirmation modals.
- Updated `devtool/textual_control_center_runtime.py` for one widget page, incremental tables, periodic provider work off the UI thread and a searchable command palette.
- Updated `devtool/ui_core/shortcuts.py`, `devtool/ui_core/navigation.py`, `devtool/textual_control_center.py` for one coherent page and navigation authority.
- Updated Zsh completions in both `devtool/completion.py` and `completions/_devtool`. Updated existing current-behavior tests `test_pres_r02`, `test_pres_r03`, and `test_pres_x08` rather than deleting important historical coverage.
- Three new focused test files: projection/negative tests, native UI integration/contracts and isolated real Textual Pilot tests.

## Security and deliberate limits

The UI does not create a second GitHub client, store credentials, create an overlay/apply engine, bypass CI policy, force-push, merge, or mark remote completion after a POST. Native launcher v3.1+ performs source-bound preflight, overlay validation, atomic apply/commit, durable delivery session, optionally approved push/PR and required-CI observation.

No hosted CI or Android physical/device test is implied by an offline Pilot pass. GitHub Actions completed status or successful run is **not** proof of Devtool required-CI policy, PR HEAD matching, artifact authenticity, signing, device test or release qualification. If a committed required-CI policy is truly absent, v3.1 may deliver without formally qualifying CI and must report that skip as not qualified.

R1 has bounded **local** pagination of 1,000 workflows and 100 recent runs. Arbitrary-depth remote server-side run pagination, workflow input widgets generated from YAML, cancellation of specific *jobs*, hosted CI watching from inside the page, and artifact provenance/release acceptance are not claimed by this milestone. They remain native Devtool CLI capabilities where applicable.

## Validation checklist

1. Confirm exact HEAD/tree and all nine modify-preimage blobs, clean named target branch, and exact native launcher v3.1+ runtime module/CLI schema blobs.
2. Run the transport kit's `--self-test`, then the canonical native builder and `.pyz check` (which calls native Devtool overlay scaffold/validator) **before** `.pyz apply`.
3. Run current Test/Pilot regression with Textual 8.2.8: old R02/R03/X08/PRES-R02 UI and the new unit/interaction tests. Include offline, delayed responses, transient errors, pagination, modal cancellation and negative run-attempt controls.
4. After committed apply, use the durable session `status`/`deliver`/`ci`, never replay `apply`. Verify resulting Git commit, PR HEAD, hosted required CI, and separately qualified device/release evidence.
