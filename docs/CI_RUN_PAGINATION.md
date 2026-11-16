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
