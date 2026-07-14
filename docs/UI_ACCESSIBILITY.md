# Native frontend accessibility and responsive-layout checks

## Desktop keyboard map

- `Alt+1` through `Alt+7`: open Overview, Run, Artifacts, Diagnostics, History,
  Repositories, and Settings.
- `Ctrl+1` through `Ctrl+6`, `Ctrl+,`: equivalent primary navigation shortcuts.
- `F9`: open the compact HUD.
- Run: `Alt+Left` / `Alt+Right` changes stage, `Ctrl+Enter` runs from Review,
  and `Ctrl+F` focuses test search.
- History: `Ctrl+F` focuses search; focused run cards open with Enter.
- HUD: `Ctrl+C` copies, `Ctrl+L` opens the log, `Ctrl+D` toggles details, and
  Escape closes the window.

## Desktop manual matrix

Verify with the normal system font, 200% display scaling, and a large system
font. At each setting:

1. Resize the Control Center from 1260 px down to its 640 px minimum.
2. Confirm navigation collapses to icons, labels remain available as tooltips
   and accessibility names, and no primary action becomes unreachable.
3. Exercise Run, History, Artifacts, Diagnostics, and Repositories using only
   Tab, Shift+Tab, Enter, Space, and the shortcuts above.
4. Resize the compact HUD on a narrow display and confirm it stays within the
   available screen, preserves readable contrast, and exposes every action.

## Android manual matrix

Verify at default font size and the largest system font/display-size settings:

1. Enable TalkBack and traverse every bottom-navigation item and Run stage.
2. Confirm visible labels are announced with their fields and each test choice,
   history action, progress step, and overlay button has a meaningful name.
3. Confirm every actionable control has a minimum 48 dp target and primary
   navigation remains at least 56 dp high.
4. Confirm action rows stack rather than clip at large text sizes and progress
   pipelines reduce to two segments per row.
5. With a hardware keyboard, verify Ctrl+1–4, Alt+Left/Right, Ctrl+Enter, and
   Ctrl+F.
