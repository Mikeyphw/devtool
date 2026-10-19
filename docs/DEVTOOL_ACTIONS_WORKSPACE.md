# Devtool GitHub Actions local workspace — UX01

Read only, no network or authentication required. Uses the existing canonical
ACTIONS-02 dispatch receipts and ACTIONS-06 pipeline journals. It is NOT a
remote-run status or a release qualification surface.

```sh
python3 -m devtool -r . actions recent --json
python3 -m devtool -r . actions ui
python3 -m devtool -r . actions detail --kind dispatch --identity 0123456789abcdef0123456789abcdef
python3 -m devtool -r . actions detail --kind pipeline --identity 0123456789abcdef0123456789abcdef
```

In the Textual UI: `j/k` or arrow keys select, `r` refreshes, `q` exits.
Small terminals show compact rows. Identifiers shown in details are exact
journal identities, not an automatically inferred remote run. No mutations,
redispatches, background polling or implicit promotion occur.

`--json` preserves machine output. When no Textual or no TTY is available,
`actions ui` prints a compact plain-text history. Root repository identity
is obtained from the existing trusted GitHub origin parser or explicitly via
`--github-repo`. A missing/corrupt/cross-repository record cannot silently
become a green success, and state scans are bounded.
