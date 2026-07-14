# Changelog

## 1.1.0-rc.3 — 2026-07-14

- Replaced the Android stock `InboxStyle` run notification with custom collapsed and expanded `RemoteViews` matching the approved mock.
- Added state rail/badges, workspace and phase cards, test/warning/error/elapsed metrics, and mode-specific actions.
- Added per-workspace HUD payloads so notification state updates independently of aggregate progress.
- Bumped Devtool Live HUD to 1.7.2 (`versionCode 20`).

## 1.1.0-rc.1 — 2026-07-14

### Release qualification

- Added a canonical cross-package version contract and consistency gate.
- Added native Qt compile/link and offscreen QML smoke tests through CTest.
- Added reproducible Debian staging, checksums, and package payload verification.
- Added Android JVM and connected instrumentation smoke tests.
- Enforced explicit Android release signing and added `apksigner` verification.
- Added deterministic source release creation with repeat-build comparison.
- Added a GitHub Actions matrix for Python, Qt, Android, emulator, and packaging.
- Added release-candidate, migration, and manual device/scale qualification guides.

### Versions

- Devtool CLI: 1.1.0
- Devtool Control Center: 0.4.0
- Devtool Live HUD: 1.7.0 (`versionCode 18`)
