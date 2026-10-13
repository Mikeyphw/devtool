# Devtool compact job UI (Textual)

Normal interactive `build`, `test`, `validate`, `invoke`, `apply overlay`, and similar
commands use a lightweight, Rich-inspired **Textual** job display by default.
The existing isolated managed-child execution and artifact transaction, validation,
commit, and rollback implementation remains the sole source of execution truth.
The presentation may not invent percentages, sign a workflow, or convert failure
into success.

* `devtool build` — compact (default) with logs and stage progress.
* `devtool --ui compact apply overlay FILE` — compact overlay review and progress;
  confirmations and canonical rollback unchanged.
* `devtool --ui full build` — existing full Textual execution interface.
* `devtool --ui plain build` — plain stdout suitable for troubleshooting.
* `DEVTOOL_JOB_UI=full|compact|plain` — equivalent environment default.
* `devtool tui` — separate Textual control center, no Rich dashboard fallback.

Compact jobs run Textual in inline mode when the installed Textual version supports
it. If Textual is not importable, CI, JSON/machine output, non-TTY, or verbose mode
is selected, Devtool uses its existing plain presentation path. `q`/Enter closes
the **finished** view; `l` toggles logs; `r` switches between condensed activity
and raw output. Both views are bounded to 240 display lines; canonical raw logs
remain available through the managed run records. Ctrl+C twice requests cancellation
through the managed child. Closing a live view does not silently mark execution
successful. The original Rich dashboard module and its `--legacy-tui` flag are
removed. Rich remains a dependency for unrelated terminal formatting/components.

GitHub Actions dispatch and live remote job-step polling are separate issues:
the compact execution UI alone does not make a dispatch-only operation follow
GitHub jobs. Do not advertise remote step progress without receiving real steps
and their status from the GitHub API.

## Validation environment

The included V2 overlay adds real headless widget tests with Textual 8.2.8
(from the supplied `textual-bundle.zip` during development). These tests also
run with other compatible Textual versions when installed. Termux needs a
compatible Python Textual installation for interactive UI; `--ui plain` is the
supported fallback. Neither `os.link` nor a browser or desktop display is needed.
The wheel bundle is used for construction-time validation and is **not**
installed into the target repository or the user's Termux environment.
