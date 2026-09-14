# Overlay 8 of 15 — Diagnostics, redaction, summaries and reporting

## Scope

This overlay closes the complete S08 diagnostics and reporting bucket from the deduped audit roadmap. It hardens every user-facing and machine-facing diagnostics surface so status, totals, summaries, clipboard output, active-run state, and structured parser output are derived from canonical failure truth and redacted before presentation.

## Findings closed

- S08-01 — Safe runner JSON leaks secrets through clipboard JSON output.
- S08-02 — Universal `--json` envelope stdout/stderr/result surfaces were unredacted.
- S08-03 — Machine state could show a failed operation with zero diagnostics.
- S08-04 — Per-result JSON status could say `ok` despite error diagnostics.
- S08-05 — Final run logs could say status `ok` when diagnostic totals had errors.
- S08-06 — Diagnostic totals were case-sensitive.
- S08-07 — Skipped nonzero results were counted as both skipped and failed.
- S08-08 — Pytest summary extraction missed all-failed/all-skipped summaries.
- S08-09 — .NET diagnostics were missed unless paths looked like absolute POSIX paths.
- S08-10 — MSBuild warning/error summary parsing was broken by zero-count and trailing-boundary cases.
- S08-11 — Gradle structured event booleans accepted string `"false"` as truthy.
- S08-12 — Malformed active-run pid values crashed `logs active`.
- S08-R2-01 — Malformed structured test metadata crashed summary extraction.
- S08-R2-02 — Workflow JSON summary could mark a failed node as passed.
- S08-R2-03 — Artifact summary treated string `"false"` as valid/verified.
- S08-R2-04 — Legacy failure classifier misclassified interrupted/resource exits as test failures.
- S08-R2-05 — Generic parser turned success text into warnings.
- S08-R2-06 — Ruff findings were always classified as warnings.
- S08-R2-07 — Structured Gradle task-finish outcome was case-sensitive.
- S08-R2-08 — Active-log merge let stale/richer HUD status override authoritative lock status.

## Implementation summary

- Added recursive redaction through `devtool.logstore.redact_sensitive_value`, covering nested mappings, sequences, secret-like keys, text patterns, and command-argument forms such as `--token value`.
- Applied the recursive redactor to clipboard JSON, universal `--json` envelopes, runner JSON summaries, Tasker output, diagnostic issue payloads, and metadata surfaces.
- Normalized diagnostic severity accounting case-insensitively and created machine-visible error entries when a result fails without structured diagnostics.
- Made per-result machine status fail when either the return code is nonzero or error/fatal diagnostics are present.
- Corrected skipped/nonzero accounting so skipped outcomes are not double-counted as failed.
- Hardened structured test metadata parsing with integer coercion and safe fallback behavior.
- Extended pytest summary extraction for all-failed and all-skipped forms.
- Expanded .NET/MSBuild diagnostic parsing to handle relative and Windows-style paths while ignoring zero-summary lines.
- Reduced generic false positives by ignoring success/completed/done/up-to-date and zero/no-warning text.
- Classified Ruff F* and E9* diagnostics as errors while preserving lower-severity lint findings as warnings.
- Strictly coerced Gradle structured event booleans and normalized task-finish outcomes before reporting progress or artifact production.
- Prevented Gradle `didWork = "false"` from producing artifact truth.
- Ensured workflow JSON summary cannot leave a failed/error node marked as passed due to stale metadata.
- Treated artifact `valid` / `verification_requested` fields as strict booleans.
- Prioritized interruption/resource failure classifiers before test-failure classification.
- Preserved authoritative active-run lock status over stale richer HUD state while still merging safe display details.

## Validation

Focused and regression suites passed from the Overlay 8 working tree:

```text
20 passed  — tests/test_rem08_diagnostics_reporting.py
316 passed — diagnostics, summary, CLI, HUD, Tasker, Gradle progress, artifact and log regression sweep
97 passed  — additional diagnostics/progress/completion/reporting compatibility sweep
```

A clean smoke apply of the generated artifact was also performed against the Overlay 7 state and the focused S08 suite passed again after apply.

## Apply mode

This is an intermediate roadmap overlay. Apply with `--no-validate`; full cross-repository qualification is reserved for Overlay 15 of 15.
