# NEXT-10-R1: operator projections and decision contract

Scope: read-only capabilities, structured diagnostic projection, dispatch-resume decision,
40–160 column text. No Gradle invocation, journal rewrite, remote POST, device or release qualification.

## Capabilities

`devtool capabilities` preserves its original default `DevtoolCapabilitiesV1` JSON and existing
keys, while `capabilities_v2` adds `DevtoolCapabilitiesV2` information. `--format text --width 40`
selects compact text; `--format json` remains default. `--json` remains compatible.

`toolchains` is PATH observation only, never proof of qualification. A configured target may
be locally blocked because policy forbids local execution even if a tool is installed;
local and hosted status are separate. A hosted workflow's live runner, secrets and
permissions cannot be proved by offline inspection, so hosted eligibility remains UNKNOWN
until independently observed. `evidence_status=NOT_RUN` and all device/signing/release
authorization defaults remain NOT_RUN. This inventory never probes GitHub or credentials.

## Diagnostics

`error_info(code=...)` is the typed entrypoint. English message classification is only
legacy fallback. `ErrorInfoV2` retains its legacy fields and adds scope, operation, state,
severity, repository, documentation anchor and a non-retrying safe action. Invalid names,
unsafe absolute paths, credentials and raw messages are never reflected. Text rendering
uses the same code/receipt/target fields and wraps at specified width.

## Dispatch resume

`dispatch_resume_decision(request_id, state_root=...)` uses the canonical, size-limited,
symlink-rejecting receipt reader. Decisions:

- `EXISTING_CORRELATED`: directly recorded successful API correlation and exact run ID.
- `EXISTING_UNCORRELATED`: HTTP 204 or transport outcome unknown; **never POST again**.
- `REJECTED_BY_API`: API rejection is definitive; an entirely new authorized request is needed.
- `NEEDS_EXPLICIT_REVIEW`: legacy/mismatched/inconsistent record; do not auto-correlate.
- `NO_MATCH`: no exact local record (only in unbound caller discovery; no inferred success).

The read-only decision intentionally does not infer run ID or attribution from nearby
GitHub runs, source SHA, branch, timing or workflow name. `dispatch_workflow` keeps its
existing filesystem lock and POST-before-journal guarantee and only adds diagnostic output
for resumable requests. The journal itself is unchanged. `review_uncertain_dispatch`
continues to require an explicit run ID and remains non-attributing.

## Qualification gates

All source tests in this overlay are non-Gradle and local. Full source-HEAD CI24,
installed Termux/wheel, real hosted Actions and consumers require separately recorded,
exact-SHA acceptance and must not be upgraded by this work.
