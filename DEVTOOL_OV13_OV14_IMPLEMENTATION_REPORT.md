# Devtool OV-13/OV-14 Implementation Report

## Campaign scope

- **OV-13 — Android logs, privacy & Activity/UI lifecycle**: MR-041, MR-042, MR-043, MR-049
- **OV-14 — Desktop persistence, execution, GTK & packaging**: MR-044, MR-045, MR-046, MR-047, MR-048

Built on the post-OV-12 campaign state, after commit `b944052`.

## Implemented guarantees

### Android logs and privacy

- Added `AndroidPrivacyLog`, a release-gated Logcat shim.
- All remaining HUD Java call sites were moved away from direct `android.util.Log` usage.
- Debug-build Logcat messages now pass through the support-bundle redactor before emission.
- Throwable logging no longer passes raw `Throwable` objects to Logcat; only bounded, redacted summaries are logged.

### Android Activity/UI lifecycle

- `DebugWorkbenchActivity` now preserves and restores scroll position across lifecycle recreation and diagnostic UI rebuilds.
- `RepositoryDetailActivity` now preserves and restores the selected tab and scroll position across lifecycle recreation and repository UI rebuilds.
- Repository-detail restoration avoids retaining stale detached scroll views when a repository disappears.

### Desktop persistence and execution

- `DesktopPreferences.save()` now writes through a same-directory temporary file, fsyncs the payload, atomically replaces the destination, fsyncs the containing directory, and enforces private `0600` mode.
- Desktop recent repository persistence now uses the same atomic, private-mode JSON writer instead of direct `write_text`.
- Desktop-launched run output is bounded in memory for aggregate output, stdout, stderr, diagnostics, and UI callbacks.
- Desktop run log lines are trimmed before persistence while preserving redaction.
- Desktop failure HUD state is written atomically with private mode so failed-run evidence is not left half-written.

### GTK packaging

- `package-deb.sh` now rejects unsafe VERSION/ARCH values before composing Debian metadata and output artifact paths.
- The package script rejects newline, slash, and backslash injection in package metadata fields.

## Verification

Focused validation command:

```bash
python3 -m compileall -q devtool tests
python3 -m pytest -q \
  tests/test_ov13_ov14_android_desktop_seal.py \
  tests/test_android_debug_phase41.py \
  tests/test_android_debug_phase42.py \
  tests/test_android_debug_phase43.py \
  tests/test_android_debug_phase44.py \
  tests/test_android_debug_phase45.py \
  tests/test_android_debug_phase46.py \
  tests/test_android_debug_phase47.py \
  tests/test_android_debug_phase47b_javac_hotfix.py \
  tests/test_android_debug_phase47c1_security_lint.py \
  tests/test_android_debug_phase47c2_c3_lint_cleanup.py \
  tests/test_android_debug_phase47c4_path_data_rules.py \
  tests/test_android_debug_phase47c5_c7_lint_cleanup.py \
  tests/test_desktop_gtk_phase8_9_final.py \
  tests/test_desktop_gap_closure.py \
  tests/test_desktop_gtk_phase0_1_startup_logging.py \
  tests/test_desktop_gtk_phase1_2.py \
  tests/test_desktop_gtk_phase2_3.py \
  tests/test_desktop_gtk_phase3.py \
  tests/test_desktop_gtk_phase4.py \
  tests/test_desktop_gtk_phase4_5_truthful_hud_control_center.py \
  tests/test_desktop_gtk_phase5.py \
  tests/test_desktop_gtk_phase6.py \
  tests/test_desktop_gtk_phase6_7_run_theming.py
```

Result:

```text
131 passed
```

`python3 -m compileall -q devtool tests` also passed.

## Deferred campaign validation

Apply this overlay with `--no-validate` during the staged remediation campaign. Final release-seal validation remains deferred until OV-16 is applied.
