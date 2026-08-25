# Architecture final seal

This seal closes the eight-overlay architecture roadmap. It adds the missing baseline/golden contracts, feature flags, event consumers, canonical result adapters, logging modes, plugin API versioning, a package-owned Android standalone engine with a thin compatibility facade, broader cleanup recognition, and an `architecture-final` validation workflow profile.

The Android standalone engine remains intentionally self-contained in `devtool/android/_standalone.py` so chroot staging can copy one executable Python source file. `devtool/android_build.py` is now only a compatibility facade; stable package-domain imports remain under `devtool.android.*`.

`devtool validate --profile architecture-final` selects the final architecture-oriented workflow policy. The repository test suite contains direct contracts for narrow terminal layouts at 40, 60, 80, and 120 columns, cleanup safety, event delivery, plugin compatibility, facade behavior, feature flags, and golden output snapshots.

The seal also removes tracked historical `*.before-*` and backup snapshot trees from the active source tree. Git history remains the durable archive, while future transient backups are handled by `devtool cleanup` under ignored `.devtool/history/` state.

Universal `--json` wraps ordinary commands. The established validation-control API is already a stable machine envelope and therefore passes through unchanged, preventing double-wrapping and preserving `--machine-file`/stdout equality.
