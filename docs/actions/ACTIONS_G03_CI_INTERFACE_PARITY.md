# ACTIONS G03 — Pipeline inspector and cross-surface stop-request parity

## Baseline and truth ownership

Baseline: exact source bytes produced by the admitted G00+G01 and G02 overlays on the user's `e723356` snapshot. G02 journal (`ACTIONS-06`) remains authoritative and the side-effect-free ACTIONS-07 projector classifies states. This overlay does **not** create a new scheduler, GitHub provider, release signer or persistent UI store.

## Delivered

1. Interactive terminal `devtool actions pipeline status --pipeline-id ID`: when a human has a TTY and Textual is available, opens the dedicated **read-only CI pipeline inspector**. It shows source commit, repository, all node states and sanitized details, clear terminal-state notices, and `NOT QUALIFIED` release status. `r` refreshes the exact journal; `q` closes the screen. Refresh errors preserve the last trusted snapshot. No mutations, implicit resume, remote cancellation, or guessed run IDs are performed.
2. Machine/JSON and redirected output are unchanged. CI/non-TTY/plain display falls back to the existing textual or machine representation; the new interactive screen only runs for explicit pipeline **status** queries, not dispatch, resume, cancel, or watch operations.
3. Desktop GTK command composer includes `pipeline-cancel` alongside plan/run/status/resume; its typed argv requires a validated exact pipeline ID and explicit fresh `--yes`. Switching to any mutating pipeline operation clears inherited confirmation. No shell interpolation or UI-side journal write.
4. Android HUD builder/importer and review copy recognize the same `actions pipeline cancel --pipeline-id ID --yes` contract and warn that this **only stops future scheduling**, not already running GitHub jobs. Import/export tests use actual compiled Java.
5. The G2 journal and previous ACTIONS-10 remote unsigned build, verified import, local LIFE signing and Termux debug baseline are untouched.

## Acceptance and qualification limits

- Positive: narrow 34x24 headless Textual screen; refresh transitions waiting → success; explicit release remains unqualified; JVM Android command construction/import round-trip; GTK typed command parity; G2 provider/dependency regressions.
- Negative: refresh failure keeps previous state; injected ANSI/control characters are not interpreted as terminal instructions; invalid pipeline ID or omitted mutation confirmation is refused; non-TTY/JSON never launches Textual.
- **Still pending:** real on-device visual/ergonomic acceptance in Textual, GTK desktop, and Android HUD; foreground/background visibility/reconnect and G2 cross-process run-store correlation; real GitHub G1 negative and G2 live matrix remain explicitly deferred. Screenshots/installed version proof cannot be inferred from these source tests.
- **Not part of this overlay:** Google/GitHub paid-private branch policy, GitHub protected signing, remote job cancellation, speculative automated resume/retry, broad release sealing, original V105 whole-campaign gate closure.

## Operator checks (after successful install)

```sh
devtool actions pipeline status --pipeline-id YOUR_PIPELINE_ID
# r refreshes; q quits. Machine remains deterministic:
devtool actions pipeline status --pipeline-id YOUR_PIPELINE_ID --json
# Explicit, separate stop request; does not terminate existing GitHub jobs:
devtool actions pipeline cancel --pipeline-id YOUR_PIPELINE_ID --yes
```

The latest source guard is on file **bytes**, not an assumed post-overlay Git SHA. On any divergence, stop and provide a new source snapshot rather than bypassing the guard.
