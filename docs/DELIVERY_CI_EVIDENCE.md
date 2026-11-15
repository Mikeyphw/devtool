# S4 delivery CI evidence and diagnostics

This is the **current** required-CI acceptance contract. CI and release remain
independent. The source of expected workflows is the exact committed
`config/delivery-required-ci.json` policy, not a snapshot of observed jobs.

## Required-CI success is fail-closed

For each required workflow, `delivery session ci` must independently:

1. Check the committed policy and bound source/branch/PR identities.
2. Find a named run with matching workflow ID and `pull_request` event.
3. Fetch **that exact GitHub run ID** and reject changed run identities, source
   SHA, attempt number, workflow ID, status or conclusion.
4. Fetch **jobs from the selected run attempt**. Refuse empty, incomplete,
   wrong-attempt, failed or unfinished job evidence. At least one job must
   succeed; skipped/neutral jobs do not imply success on their own.
5. Recheck the current policy digest and the remote branch / PR merge identity
   after the provider requests, before saving a passing receipt.

`CHECKS_EVIDENCE_PENDING` is a durable, non-green, bounded polling state.
GitHub API outage, rate limiting, empty job inventory, attempt mismatch,
truncation, or a changed exact run must never produce a success. No workflow
redispatch or rerun is attempted in this phase. The prior session and receipt
remain available for explicit operator recovery.

## Failures

For a failed workflow, S4 saves bounded *job/step metadata* only, including
exact attempt IDs, job URLs, failure classification **hints** and step names.
A hint is not a proven root cause; the linked hosted job log is authoritative.
No raw logs, tokens, or signed log download URLs are persisted. API errors
become an availability diagnostic, never a green check.

## Native CLI / Rich launcher

```sh
python3 -m devtool --repo "$HOME/Code/devtool" delivery session ci \
  --id SESSION_ID --checks 25 --interval 8
```

Use the Python/Rich S4 launcher to apply with Devtool and to resume CI after
Devtool exits. It uses the same native run+attempt evidence in its receipt. An
existing S3 session is bound to an earlier SHA; create a **new S4 session**
after successful application. Re-applying the overlay is not a resume operation.

A `REQUIRED_CHECKS_PASSED` receipt asserts required CI only. It does **not**
authorize publishing, signing, installation, device acceptance, or a release.
