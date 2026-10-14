# G3 narrow Textual node visibility — practical device remediation

Observed on rooted Android/Termux, 2026-10-13: read-only persisted G2 journal passes strict source/run/archive identity guards; real Textual PTY at 92×28 passes, while at 48×20 `archive` never appears in captured frames despite wheel, keyboard paging, and End, with the other three nodes observed.

This change is **not** a claim that the mouse/keyboard scrolling implementation is faulty: PTY timing may also have contributed. To make the useful result independent of scroll position, the Textual inspector now defaults to compact rows when terminal width ≤60 or height ≤22, one node per line, with the existing state symbol, complete state string and node ID. At larger sizes, the original detail-rich rendering is retained. `d` toggles expanded details on any size, `r` remains read-only refresh, and `q` quits. A resize recomputes the presentation from the last trusted in-memory status; no pipeline operation, journal write, remote query or GitHub dispatch is triggered by the UI.

Safety remains: every node derives from ACTIONS-07 projection, refresh still identity checks, untrusted node text is sanitized without Rich markup parsing, cancellation and blocked/waiting warnings remain truthful even in compact mode, and every screen says `Formal release: NOT QUALIFIED`. The compact source digest is pinned to Devtool commit `d9fbc6b5c2beacae81f13b2106d696375258d4ae` and refuses unrelated local source edits.

Unit tests cover compact node completeness, preservation of warnings/sanitization, details toggling and actual narrow Textual layout when Textual is installed. The external Termux acceptance harness must still run after installation, because tests on a non-Android host cannot establish actual Termux behavior. The original G2 pipeline and GitHub artifact remain unchanged.

This overlay deliberately touches only `devtool/actions_pipeline_textual.py`, adds its focused regression tests and this documentation. It does not change pipeline execution, GitHub workflow files, Android APK or release policy.
