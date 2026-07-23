# UI regression protection

Slice 4 adds one repeatable regression contract for the terminal, Qt Control
Center/HUD, and Android frontend.

## Golden snapshots

`tests/snapshots/ui/` contains two kinds of snapshots:

- Real Rich terminal output captured at a fixed 88-column width.
- Deterministic SVG visual-state images for desktop HUD, desktop page, Android
  notification, and Android overlay scenarios.

The SVG snapshots intentionally avoid platform fonts and native widget raster
output. They remain byte-stable in headless CI while still producing images
that can be opened and visually diffed. Native Qt/Android screenshot jobs can
use the same scenario IDs as their fixture names.

Update snapshots only after reviewing the resulting diff:

```bash
python3 tools/update-ui-snapshots.py
pytest -q tests/test_ui_ux_slice4.py
```

## State scenarios

The shared contract lives at:

```text
devtool/resources/ui/ui-regression-contract.json
```

It defines canonical labels, forbidden stale phrases, performance budgets, and
these required state families:

- Multiple concurrent runs with independent progress.
- Apply failure while rollback is active.
- Successful rollback reported as `Changes rolled back`, not as another error.
- Run review, searchable History, Android grouped notification, and overlay
  terminal states.

## Terminology consistency

The Slice 4 tests compare the shared contract with Python, QML, and Java source.
They prevent old .NET-only or GTK-era labels from returning and require the same
runner labels in the desktop HUD and Android presentation layer.

## Performance budgets

The suite uses deliberately large fixtures and measures only the operation under
test, not test-data construction or Java compilation:

| Path | Fixture | Budget |
|---|---:|---:|
| Desktop History reference/index | 20,000 records | 750 ms |
| Python diagnostics parser | 25,000 diagnostics | 1,800 ms |
| Android test catalog | 12,000 test IDs | 2,500 ms |

The budgets are regression tripwires rather than microbenchmarks. They are loose
enough for normal CI variance but low enough to catch accidental quadratic
filtering, repeated case conversion, or giant concatenated buffers.

## Native screenshot matrix

Where Qt 5 and the Android SDK/emulator are available, capture the same scenario
IDs at:

- Desktop: 640×720, 900×720, and 1440×900; 100% and 200% scaling.
- Android: phone portrait, phone landscape, and tablet; normal and 1.5× font.
- Themes: light and dark.
- Inputs: keyboard-only desktop and TalkBack traversal on Android.

Native captures are supplemental. The portable snapshots and state tests remain
mandatory on every platform.


## UI/UX roadmap final contract

The final roadmap gate is `tests/test_ui_contract_finalization.py`. It ties together the accepted contracts from the HUD parity/autosize, typography/navigation, Overview/Run-flow, detailed HUD, and notification accessibility overlays.

This test is intentionally source-contract based so it can run on Termux/chroot systems without a Qt display server or Android emulator. Native screenshot captures remain supplemental, but the final contract must verify the following on every platform:

- Desktop HUD content-aware auto-size remains present and HUD detail is not removed.
- Android floating HUD persists and clamps its position.
- Android notification compact, expanded, and large-text layouts keep status lines below the progress rail.
- Notification progress rails, state-label prominence, content descriptions, and 48 dp action targets remain in sync with `notification-ui-contract.json`.
- Overview remains action-first and Run remains progressively disclosed.
- User-facing navigation keeps the Logs label and compact Android page-title scale.
