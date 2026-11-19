# Source-bound GitHub CI registration pagination

Devtool reads only the workflow runs belonging to the **delivery session's**
fixed registration interval (session start minus 120 seconds through the lesser
of deadline/current time plus 120 seconds), filtering the GitHub Actions API
by exact event and `created` range. It does not search 500 unrelated historical
runs or select PR checks from a synthetic union of sources.

Every returned run is checked for exact event, bounded timestamp, valid unique
run ID and complete API pagination. Exact PR, source SHA/merge SHA, workflow ID,
attempt/jobs, ruleset and committed policy checks remain mandatory.

If the scoped result reaches the 500-run safety cap, GitHub changes the
`total_count` during pagination, a page is missing, or filters are ignored,
Devtool refuses CI qualification. Provider failures never become green results.

A delivery that was already committed and pushed must **not** reapply the
original overlay after a CI observation failure. Correct the registrar in a
new source-bound native overlay and establish a new durable session for the
new source HEAD. The prior session is retained for read-only status/report,
but its old SHA cannot qualify CI once the feature branch has moved.
This change never merges, force-pushes, disables checks, or qualifies releases.

## Exact ruleset reads and durable native push proof

The registrar must request GitHub's effective `rules/branches/{branch}` API
through the canonical GitHub provider's GET-only allowlist. Base branch `/`
characters are encoded as `%2F`. No arbitrary URL, write method, redirect,
external app context, invisible protected-branch policy, or failed provider
request is accepted as an empty or passing ruleset. Exact current-attempt job
and committed CI policy checks still determine qualification.

After native `delivery session push` independently observes the current origin
ref at the session's exact HEAD, the existing `confirm_intent()` journal
persists `remote_verified=true` only for a matching, already authorized push
intent and immutable ref/HEAD key. Read-only push reconciliation upgrades an
older confirmed intent idempotently after the same remote-head read. Missing,
conflicting or uncertain push intents never create remote evidence. PR and CI
observation alone are not substitutes for this source-bound push proof.

A committed PR feature branch must never be reset or its existing delivery
session rebound when correcting a later status issue. A new native overlay
advances the branch, makes a new source-bound session, and requires new PR-HEAD
CI checks. The manual 24-shard regression remains a separate B3 acceptance
gate; successful required PR workflows do not imply 24-shard qualification.
