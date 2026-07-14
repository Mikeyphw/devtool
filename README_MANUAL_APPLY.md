# Manual apply

This archive is the complete Devtool 1.1.0 repository after UI/UX Slices 1–4
and Slice 5 native release qualification. Extract it as a replacement source
tree; it is not a partial overlay.

After extraction, run:

```bash
python3 -m pip install -e . pytest
python3 tools/release-qualify.py --core-only
```

On a release host with Qt 6 and Android SDK 36, use:

```bash
python3 tools/release-qualify.py --require-qt --require-android --device-tests
```

Android release tasks require explicit `DEVTOOL_RELEASE_*` signing values. See
`docs/RELEASE_QUALIFICATION.md` and `docs/RELEASE_CHECKLIST.md`.
