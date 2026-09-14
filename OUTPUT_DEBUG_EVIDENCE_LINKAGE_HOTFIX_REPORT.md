# Output Debug Evidence Linkage Hotfix

Post-final evidence audit found that terminal-summary and clipboard payloads were internally correct and matched the referenced logs when those logs were present, but some output-debug metadata pointed to logs that were not copied into the per-run evidence directory. This hotfix makes future output-debug evidence self-contained.

## Fixed

- Every terminal-summary and clipboard output-debug capture now attempts to preserve its referenced log under `evidence/<run_id>/logs/<log basename>`.
- Output-debug metadata now records `log_preserved`, `evidence_log_file`, `evidence_log_bytes`, and `evidence_log_sha256`.
- `evidence/<run_id>/evidence.json` is maintained for successful and failed runs, with an `output_debug` section listing captures and preserved logs.
- Clipboard success summaries now distinguish clean success from success with warnings. Runs with warnings start with `Devtool completed with warnings.` instead of the less precise clean-success headline.
- Failure-evidence path discovery now recognizes output-debug `log_file`, `payload_file`, `metadata_file`, and `evidence_log_file` fields so archived failure evidence can carry the debug payload/log chain forward.

## Validation

Local validation performed in the reconstructed final repo state:

- `python3 -m py_compile devtool/cli.py devtool/validation_evidence.py tests/test_output_debug.py`
- `python3 -m pytest -q tests/test_output_debug.py` → 13 passed
- `python3 -m pytest -q tests/test_output_debug.py tests/test_log_audit_followup.py tests/test_cli.py` → 161 passed, 8 subtests passed

## Limitations

This fixes future evidence generation. Existing evidence archives cannot be made fully self-contained unless the original referenced log files still exist on the device and a separate repack/import tool copies them into the old evidence tree.
