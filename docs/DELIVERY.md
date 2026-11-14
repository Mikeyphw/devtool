# Delivery sessions (current contract)

Devtool delivery sessions are durable coordination records. They reuse, and never
replace, Devtool's artifact transactions, Git operations, validation sessions,
GitHub Actions dispatch receipts or exact-run observation.

## Available now: planning and journaling

```sh
# Read-only and does not create a delivery journal
python3 -m devtool --repo "$HOME/Code/devtool" delivery session plan --base main

# Creates only a private PLANNED journal; does not apply/push/dispatch
python3 -m devtool --repo "$HOME/Code/devtool" delivery session start --base main

python3 -m devtool --repo "$HOME/Code/devtool" delivery session status --id DELIVERY_ID
python3 -m devtool --repo "$HOME/Code/devtool" delivery session resume --id DELIVERY_ID
python3 -m devtool --repo "$HOME/Code/devtool" delivery session report --id DELIVERY_ID
```

`--head` chooses a branch only when it is the checked-out branch; `--overlay`
adds a read-only SHA-256 inventory. Default output is human-readable; `--json`
is optional. Sessions are private under
`${XDG_STATE_HOME:-~/.local/state}/devtool/delivery/sessions/` and are bound to
worktree root, Git directory, head/base branches, exact local commit and
(optional) overlay SHA-256. `start` is not equivalent to applying the overlay.

`resume` inspects source drift and unresolved operations. It **never** executes
a remote mutation. The internal `record_intent` API journals authorized
non-idempotent operations before a network request and deduplicates operation
identities; any unresolved operation requires reconciliation and is never
implicitly retried. GitHub `workflow_dispatch` 204/uncertain outcomes remain
uncorrelated, as required by the existing Actions contract.

Every journal is integrity-checked, atomically replaced, fsynced on POSIX and
written with private file/directory permissions and an interprocess lock.
Symlinks, corrupt records, unexpected repository/source drift and missing
explicit authorization are rejected. Journal integrity checks detect accidental
corruption, not malicious editing by the file owner.

## Not yet implemented

This slice contains **no** automatic overlay apply, remote push, PR create/reuse,
CI registration, check supervision, or release qualification. The delivery
session will gain authenticated stages that consume canonical Devtool receipts
in subsequent work. No stage is reported successful simply because an operation
was planned or accepted by GitHub.

## Current-state test policy

Maintain tests by active capability (`tests/test_delivery_session.py`). Replace
or delete obsolete executable contracts rather than preserving tests solely to
replay past milestone behavior. Keep audit evidence and regressions for actual
current safety boundaries. Android Gradle execution remains hosted in GitHub
Actions, not Termux.

Existing `devtool delivery plan ARTIFACT` remains the authoritative LIFE artifact qualification command. The new delivery **session** namespace does not change that interface.
