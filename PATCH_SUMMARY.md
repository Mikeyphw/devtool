# Devtool typography remediation patch

This complete repository builds on the Slice 5 notification-parity baseline and
implements the full typography audit across Android, the Qt Control Center, the
compact HUD, and notification `RemoteViews`. The Android frontend is 1.7.2
(`versionCode 20`) and the release candidate is `1.1.0-rc.3`.

## What changed

- Added centralized Android semantic roles for page titles, card titles, body,
  captions, labels, buttons, metrics, and code.
- Removed page-local raw Android text sizes and the global cramped line-spacing
  multiplier. Multiline body content now uses role-specific leading.
- Raised notification title/detail/badge/action sizes, increased secondary-text
  contrast to approximately 5.45:1, and removed fixed-height text rows.
- Added a simplified expanded notification for `fontScale >= 1.3` with reduced
  phase density and one combined metric summary.
- Preserved 48dp notification actions and 56dp Android navigation targets.
- Replaced Android and desktop navigation/status/action font glyphs with vector
  assets or shape-based marks.
- Made desktop typography derive from the platform UI font and added persistent
  100%, 125%, 150%, and 200% user scaling.
- Increased desktop body/compact line heights, removed derived 8pt operational
  text, reduced heading tracking, and converted headings to sentence case.
- Switched code text to the system fixed-width font instead of requiring a Nerd
  Font.
- Added typography-system documentation and regression coverage for minimum
  sizes, contrast, scale behavior, vector icons, and semantic role use.

## Validation

- Full repository suite: **449 passed plus 28 subtests**.
- Typography remediation module: **7 passed**.
- Targeted typography/notification/accessibility set: **29 passed**.
- Python compilation, shell syntax, 7 JSON files, 58 Android XML resources, and
  the GitHub Actions workflow passed validation.
- Portable release qualification passed.
- Deterministic source release generation matched byte for byte.
- Android APK compilation was not run locally because no Android SDK is
  installed. Qt linking was not run because Qt 6 development files are absent.
