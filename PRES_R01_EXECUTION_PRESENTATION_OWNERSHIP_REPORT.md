# PRES-R01 — Canonical Textual Execution-Presentation Ownership Remediation

**PRES-R01 — gate-window overlay 1 of 4; implementation 1 of 3; full-plan 61/76.**

PRES-R01 is the first implementation overlay in the reopened PRES/Textual runtime remediation window. It does not restart or replace PRES-X01..X08/G1/G2 history. It corrects one runtime boundary those overlays failed to qualify: normal Devtool execution still let the Gradle, .NET, npm and generic progress trackers own `rich.live.Live` directly.

## Delivered architecture

Normal execution now follows one renderer-neutral flow:

```text
runner / workflow / Gradle / npm / .NET / generic output
                         |
                         v
             semantic progress trackers
                         |
                         v
             canonical snapshot callbacks
                         |
                         v
          ExecutionPresentationController
                   |               |
                   v               v
             Textual TTY       plain/stream
             presentation      non-TTY / CI /
                               verbose / machine
```

The trackers parse progress and publish snapshots/events only. They no longer instantiate `Rich Live`, own terminal inspection loops, print completion lines, or own cancellation callbacks. Rich remains available for renderer-neutral formatting helpers where useful, but `Rich Live` is no longer the hidden renderer for normal Gradle/.NET/npm/generic work or embedded artifact preflight/validation/finalize hooks.

The runner continues to own the process, return code, captured output, durable log, execution adapter, RUN/EXO events and cancellation. The presentation controller receives copies of semantic snapshots and display lines; its failure is secondary and falls back to plain output.

## Textual execution surface

The interactive execution surface is a concrete Textual app, not a model record. It uses real Textual widgets for status, progress, bounded live log and cancellation notice. A bounded queue separates the runner thread from the Textual event loop. Presentation backpressure may drop intermediate UI updates but cannot block process execution.

Ctrl+C is consequential: the first press arms cancellation and the second press within three seconds invokes the runner-owned cancellation callback. The presentation surface does not kill processes itself.

## Output safety

TTY presence alone does not authorize Textual. CI, verbose, redirected/non-TTY, explicit plain, quiet and structured/machine-oriented CLI modes force plain presentation. Captured output and durable logs remain complete even when Textual hides the duplicate raw interactive stream.

Tasker/HUD remains a parallel projection subscriber. It receives the same semantic progress without stealing local terminal ownership. Embedded artifact hooks use the same controller: their `GenericProgressTracker` parses semantic state, the controller owns display selection, complete child output remains durable, and Ctrl+C delegates to the existing hook process authority.

## Historical qualification correction

The existing PRES-X04..X08/G1/G2 artifacts and reports remain immutable historical evidence, but runtime completion claims are reopened where they were based on model/spec/`--once` behavior rather than the actual UI. The refreshed active remediation order is recorded in the single canonical amendment `PRES_REMEDIATION_IMPLEMENTATION_ORDER_20261001.md`; no parallel PRES roadmap is created.

PRES-R01 does **not** claim the `devtool tui` control center is repaired. That concrete application boundary belongs to PRES-R02, followed by PRES-R03 parity/real-app qualification and a separate PRES-G2R requalification gate.

The repository still contains `Rich Live` in the explicitly selected legacy terminal/studio HUD renderers. Those are not the normal execution owner addressed by R01; their cross-surface migration/deprecation/qualification remains open for the later PRES remediation/gate path and is explicitly tracked rather than hidden.

## Targeted validation and deferred authority

Construction-host targeted validation covers the execution controller, semantic
trackers, generic/Gradle/npm/.NET runner regressions, artifact lifecycle hooks,
structured event transport, Termux runner contracts, diagnostics/progress
contracts and campaign-ledger invariants. The final construction run completed
**211 passed, 0 failed, 1 skipped**; the sole skip is the concrete Textual
`App.run_test()`/Pilot proof because Textual is not installed on this construction
host.

The artifact's native `pres-r01` workflow sets
`DEVTOOL_PRES_R01_REQUIRE_TEXTUAL=1`, so that same concrete-app test is mandatory
on the native Termux qualification host and cannot silently skip. Broader
PRES-X04..X08/G1/G2 concrete-control-center requalification is intentionally
**deferred to PRES-G2R** after R02 and R03; final cross-surface authority remains
**PRES-G3**.


## PRES-H01 runtime correction — Textual main-thread safety

Native Termux runtime exposed a false-positive in R01 qualification: the concrete
`ExecutionProgressApp` was launched by `_TextualExecutionPresenter` in a helper
thread. Textual 8.2.8's Linux terminal driver installs process signal handlers, so
Python raises `ValueError: signal only works in main thread of the main interpreter`.

PRES-H01 removes the background `App.run()` launch entirely. The synchronous
execution controller now fails closed to the existing plain/machine-safe stream when
a live Textual terminal host is requested, while retaining canonical semantic
tracking, logs, evidence and the concrete Textual app/Pilot tests. This is a safety
hotfix, not closure of the live Textual ownership promise: PRES-R01-P003 is reopened
until the campaign supplies a main-thread Textual host with execution work moved
off the UI thread. Rich Live is not restored.
