# Release verifier and final-seal integrity — Overlay 2 of 15

This overlay closes the S16 release-integrity findings assigned to Overlay 2 of 15.

## Findings closed

- S16-01: synchronized `completions/_devtool` with `devtool.completion.ZSH_COMPLETION`, restoring FS03 `completion.synchronized`.
- S16-02: final seal now content-validates reports, docs and roadmap contract tests rather than checking paths only.
- S16-04: final qualification reports validate existing final JSON and reject stale mismatched evidence instead of silently reusing it.
- S16-05: release artifact discovery is restricted to an explicit current-candidate manifest, so stale files already in the output directory cannot qualify.
- S16-10: root module verification is based on archive content (`module.prop`, `customize.sh`, `bin/devtool-root-bridge`) rather than filename.
- S16-R2-01: wheel verification now requires a real `.dist-info` directory and valid `WHEEL`, `METADATA` and `RECORD` content.
- S16-R2-02: ZIP-family and tar-family artifacts reject absolute, traversal, drive-prefixed, backslash-normalized, and unsafe link member paths.

## Regression coverage

Added `tests/test_release_verifier_final_integrity.py` and updated the existing OV15/OV16 release-seal tests to use valid wheel fixtures and the explicit current-artifacts manifest.

Focused local validation executed in the generation environment:

```text
python3 -m pytest -q tests/test_release_verifier_final_integrity.py tests/test_fs03_final_release_seal.py tests/test_ov15_ov16_release_final_seal.py
```

The overlay is an intermediate campaign overlay, so normal application should use `--no-validate`; Overlay 15 of 15 remains the complete repository-wide validation seal.
