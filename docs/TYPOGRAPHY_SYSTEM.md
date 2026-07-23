# Typography system

Devtool uses semantic type roles rather than page-local numeric font sizes. The
roles are shared conceptually across Android, the Qt Control Center, and the HUD.

## Android roles

| Role | Size | Line-height multiplier | Typical use |
| --- | ---: | ---: | --- |
| Page title | 24sp | 1.20 | Compact phone and app screen heading |
| Large value | 20sp | 1.18 | Primary metric |
| Card title | 16sp | 1.25 | Card and dialog heading |
| Body | 14sp | 1.40 | Descriptions and multiline content |
| Body small | 13sp | 1.36 | Dense supporting content |
| Button | 14sp | 1.20 | Actions |
| Label | 12sp | 1.25 | Status and field labels |
| Caption | 11sp | 1.30 | Secondary metadata |
| Code | 13sp | 1.36 | Commands, paths, and logs |

The notification uses 14sp titles, 12sp workspace labels and actions, 11sp
details, and 11sp dominant state badges. At `fontScale >= 1.3`, it switches to a simplified
expanded layout that reduces phase density and combines metrics into one line.
Text rows use `wrap_content` and minimum heights instead of fixed heights.

## Desktop roles

The QML theme derives its base from `Qt.application.font.pointSize` and clamps
the user scale between 100% and 200%. The Settings page exposes 100%, 125%,
150%, and 200% presets. System general and fixed-width fonts are preferred; UI
semantics do not depend on Nerd Font glyphs.

## Rules

- Do not introduce raw Android `setTextSize` calls outside `UiTypography`.
- Do not use operational text below 10sp in notifications or below the theme's
  caption role on desktop.
- Use sentence case for section headings; reserve uppercase for short state
  badges and compact metric labels.
- Use vector drawables or `StatusMark` for icons and state marks.
- Verify layouts at font scales 1.0, 1.3, 1.5, and 2.0 before release.


## UI/UX roadmap finalization

The final polish stack standardizes these additional rules:

- Desktop metric and section labels use sentence case unless the text is a compact state badge.
- Android page headers stay compact on phones; large overline branding is reserved for wider layouts.
- The Logs label is the user-facing destination name even when source files keep Diagnostics implementation names.
- Notification state labels are allowed uppercase because they are compact state badges.
- HUDs may contain dense operational detail, but headings and grouping labels must create a clear scan path.
