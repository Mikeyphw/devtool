# ACTIONS Live Progress — compact Textual (Termux)

This is a **foreground UI projection** of GitHub Actions observations, not an
alternate remote build runner, release qualification engine or artifact signer.

- In a TTY, `devtool actions android unsigned-run --ref main --yes` dispatches the
  existing source-correlated unsigned workflow and *then follows its exact run*
  in the compact Textual job UI. It never selects the newest workflow by guess.
- `devtool actions watch --request-id ID` / `--run-id ID` opens the same view.
  Status, jobs and steps come from GitHub's run/jobs endpoints; Gradle progress
  stays indeterminate unless explicitly reported by GitHub.
- `--ui plain` (or a non-TTY/CI/`--json`) preserves the existing non-interactive
  command behavior; it does **not** automatically follow dispatch. Machine JSON
  payloads remain unchanged, except a watched remote failure/timeout now has a
  truthful nonzero exit code.
- In the compact UI `Ctrl+C` twice **detaches local monitoring only**. It does
  not call GitHub cancel. A subsequent watch with the dispatch request ID or run
  ID can reconnect. Failed jobs/steps appear in Activity and Raw views.
- Remote failure and SHA mismatch result in exit 1. A polling deadline that
  expires before confirmed remote completion results in exit 2, not success.
- UI events are bounded single-line JSON on the managed child pipe. The parent
  only renders them. Remote workflow authorization, source identity, API errors,
  status truth and receipts remain in the canonical Actions modules.

Termux: uses Python stdlib, Textual and normal GitHub API clients. No AF_UNIX
sockets, daemons, extra shell dependencies, or terminal width assumptions are
introduced. Compact mode now uses a clean alternate screen rather than the
previous clipped inline display; the shell screen is restored on exit.

The separate GitHub Actions logs ZIP retrieval is unchanged: GitHub doesn't
provide arbitrary live Gradle stdout through the run/jobs REST endpoints.

## Completion and display safety

The parent drains the bounded stdout reader before presenting the child exit
code. Repeated step names in separate jobs remain distinct; job/step changes are
activity lines, never inferred build percentages. A local double Ctrl+C reports
exit 130 even when the Python child maps SIGINT to exit 1. This does not send a
GitHub run-control API request. If dispatch is interrupted before the correlated
run ID is received, the remote outcome remains **unknown** and must be checked
using the local dispatch receipt before retrying.

The overlay updates the pre-V2 Textual CSS contract test to verify the
current `96%` phone-safe modal width and scrollable controls; it no longer
excludes that test from targeted validation.

## Compact terminal and color display

The compact Textual job UI uses the full terminal viewport while a job is
active, with flexible log height and responsive small-screen controls. The
artifact apply view includes a stage list when height permits; those stages
show only observed transitions, never fabricated completed steps. Long
confirmation content stays scrollable with reachable confirmation buttons.

Both job and apply RichLog widgets accept safe Rich Text built from ANSI SGR
styles. OSC/title/link changes, screen-clearing, cursor positioning, and other
non-SGR controls are removed before rendering; raw persisted logs remain
unchanged. The child sets `FORCE_COLOR=1` only when `NO_COLOR` is absent and
no explicit `FORCE_COLOR` preference exists. This lets Python Rich retain
bold/color styling when subprocess stdout is captured. Default Unicode icons
work in standard Termux; set `DEVTOOL_NERD_FONT=1` to opt into patched-font
icons. The UI is a renderer, never a source of authoritative job status.

## Audit corrections in V3 UX

A normal compact job now shows recently observed canonical phase names, and
GitHub monitoring has a dedicated stage list made from actual job and step
statuses (not just free-form log messages). The progress bar is indeterminate
when the canonical producer cannot provide a percentage. No phase advancement
is labeled successful without a terminal result or explicit GitHub conclusion.

Interactive `actions watch` follows for up to 120 checks at 10-second intervals
by default (maximum 20 minutes); explicitly supplied `--interval` and
`--max-checks` values remain authoritative. Noninteractive and `--json`
commands keep their existing bounds and output. An unfinished run at the
polling deadline returns exit 2 and can be reattached, not treated as success.
