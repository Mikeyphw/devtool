# M6 native launcher boundaries and saved inspection

This is a continuation of the real-tmux-accepted V10 source, not a new transaction engine.

- The launcher uses static, synchronous stage announcements around existing build/preflight/native validation/atomic apply/postcommit/adoption/delivery boundaries. Child streams, native code, private transcripts and exact-source receipts remain authoritative. It does not attach a second Live renderer or keyboard owner to a child process.
- The repository wrapper re-enters the existing canonical CLI once under its existing trusted invocation context. The stage label never prints arguments, paths or credentials.
- `DEVTOOL_UI_INSPECT=1` explicitly opts a human real TTY into saved evidence navigation for `delivery session status|resume|report`, `overlay plan|check|status|recovery-plan` (except explicit `--diff`) and `transaction inspect`. Ordinary commands remain nonblocking. Mutation paths do not opt into a foreground inspector. Existing JSON, quiet, NO_COLOR, redirected and machine output bypass the browser. The native return code is unchanged.
- Keys: 0 overview, 1 identity, 2 inventory/validation, 3 events, 4 native output/artifact/report references, 5 evidence, `[`/`]` or j/k page, q exit. Long values fold by Rich display width. No alternate screen, no automatic provider calls, no clipboard actions. Saved native receipts are not a fresh CI/credential verdict. A missing or ambiguous transaction/history is explicitly unavailable. The inspector uses the repository-specific active or archived journal and exact runtime history, checking native receipt identity before display.
- Existing CI observer retains the final inline frame (`screen=False`, `transient=False`); existing keyboard/redraw ownership is unchanged. Real Termux reacceptance is required because retained scrollback differs from V10.
- Native raw logs are not implicitly opened from untrusted receipt paths. The saved UI exposes native operation/history/run/report references; existing explicit source-bound CI log handling is unchanged. Raw archived log paging, live credential-health refresh, and complete ZIP/PYZ device acceptance are not claimed by this change. No hidden credential probes or synthesized qualification.

## Example (real TTY, use recorded repository and session)

```bash
DEVTOOL_UI_INSPECT=1 python3 -B -m devtool --host --repo "$PWD" --no-clipboard delivery session status --id SESSION_ID
```

Use the native JSON command without the inspector to compare authoritative evidence. `DEVTOOL_RICH_LIVE=0` disables the new wrapper/launcher stage chrome and saved inspector. The supported plain host selectors remain compatible with ordinary inline Rich output.

## Source-bound M6 inspection UX correction (post-feaed07 candidate)

- Session, transaction and overlay inspection use **distinct headers**. Their
  final frames remain in terminal history with `screen=False` and
  `transient=False`; historical frames are not evidence of two active Live
  owners. No terminal-clear escape is sent and the native record is immutable.
- Navigation hints explicitly identify `[` as previous page and `]` as next
  page. `/` has no shortcut. Compact widths preserve `q exit` on a separate row.
- `l` opens an **explicitly referenced, managed native log** only when a bound
  transaction, history or session receipt contains an admissible log path. The
  original command output remains in native logs. The viewer never discovers
  arbitrary paths, opens HTTP endpoints, mutates records or automatically reads
  logs. `[` and `]` page the redacted local preview; `n` selects the next bound
  log; `l` returns to inspection; `q` quits. Only regular non-symlink files below
  a managed native log directory and at most 16 MiB are eligible. Large, absent,
  or unmanaged log references are reported unavailable, not coerced to PASS.
  The existing native `logstore.redact_sensitive_text` is a mandatory dependency:
  without it, reading refuses rather than exposing raw secrets. Redaction is a
  display safeguard, **not** a reason to copy raw logs or credentials to clipboard.
- Saved M4 credential receipts remain **historical**. If an already supplied
  canonical `credential_health` diagnostic is present, its public whitelisted
  fields are projected separately: selected auth, installation, repository,
  required/granted capabilities, expiry, drift, fork/base and four independent
  AUTHENTICATED / AUTHORIZED_FOR_OPERATION / REMOTE_VERIFIED /
  REQUIRED_CI_QUALIFIED states. Missing evidence stays UNVERIFIED. Inspection
  performs **no hidden token refresh**, installation approval or provider probe.
  Current authorization must be established through the existing provider.
- This is an uncommitted candidate until the real post-integration HEAD/tree,
  per-file hashes, full-checkout tests, native transaction and actual Termux
  interactive behavior are independently verified. No CI24/release claim.
