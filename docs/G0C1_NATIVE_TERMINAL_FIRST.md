# C1 — Native terminal-first execution

This milestone changes the **default** for normal apply/build/validate/test/delivery/Actions
invocations to native stdout/stderr. Installing Textual, including the optional
`textual-bundle.zip` wheel bundle, does not activate an alternate-screen host.

- Normal invocation: `devtool --repo REPO apply-overlay PACKAGE` or `devtool --repo REPO build`.
  Native execution and existing receipts/transactions remain authoritative.
- Explicit opt-in Textual host: `devtool --ui compact ...` or `devtool --ui full ...`.
  Machine JSON, pipes, CI, verbose and no-TTY still refuse Textual.
- An explicit `devtool tui` and `devtool actions ui` remain user-invoked UI routes.
- Rich renders in the existing terminal only when it is a real color-capable TTY;
  `NO_COLOR`, `CLICOLOR=0`, `TERM=dumb`, and non-TTY disable it. Rich Live remains
  `screen=False`, and terminal restoration is protected by existing `try/finally`.
- `fzf` is optional and never started by the native apply or CI path.
- Clipboard writing requires **explicit `--copy`**. No implicit completion prompt,
  no copy-on-select, and old persistent `cli.output.copy` cannot authorize a
  clipboard write; `--no-clipboard` remains supported. Explicit copy still uses
  Devtool's existing clipboard backend and conflicts with `--no-clipboard`.
- In the explicitly invoked Textual Control Center, confirmed mutations must
  pass an approval marker. Delegated children write stdout/stderr to a private
  log with an operator-visible path rather than `DEVNULL`. They still use the
  original native execution and transaction authorities.

C1 does not claim C2 status-projection unification, C3 historical-gate retirement,
or G4 hosted/device/release acceptance. No Termux Gradle tests are required.
