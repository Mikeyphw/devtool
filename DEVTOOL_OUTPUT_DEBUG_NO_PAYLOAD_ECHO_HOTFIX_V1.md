# Devtool output-debug no-payload-echo hotfix v1

## Purpose

Remove the unintended terminal echo of captured output-debug payload bodies while preserving all output-debug evidence and correlation metadata.

## Behavior

`--output-debug` / `DEVTOOL_OUTPUT_DEBUG=1` still records the rendered terminal summary and exact sanitized `--copy` payload under `evidence/<run-id>/output-debug/`, including sizes, SHA-256 hashes, timeline metadata, clipboard backend/result, and `matches_terminal_summary` when available.

The terminal now prints only compact metadata such as run/output IDs, byte count, SHA-256, saved evidence path, and the terminal/clipboard match result. The captured terminal-summary or clipboard payload body is never echoed by output-debug.

This applies to both terminal-summary and clipboard captures. The payload remains available in the private `.txt` evidence file for inspection.

## Documentation

CLI help and both complete feature guides now explicitly describe output-debug as persistence/correlation without payload-body echo.

## Validation

Regression coverage verifies that terminal-summary and clipboard payload bodies are written to evidence but are not emitted by output-debug presentation.
