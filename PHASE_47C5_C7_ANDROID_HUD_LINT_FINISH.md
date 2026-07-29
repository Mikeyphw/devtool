# Phase 47C-5/47C-6/47C-7: Android HUD lint finish

This overlay combines the final lint cleanup buckets requested after Phase 47C-4.

## 47C-5 Backup rules completion + launcher/accessibility cleanup

- Adds `android:fullBackupContent="@xml/backup_rules"` to complement Android 12+ `dataExtractionRules`.
- Adds `res/xml/backup_rules.xml` with the same local-data exclusion posture used by `data_extraction_rules.xml`.
- Adds a monochrome adaptive launcher icon drawable and references it from both adaptive launcher icons.
- Replaces the draggable overlay header with `DraggableHudHeader`, which overrides `performClick()` for accessibility.
- Suppresses the intentional Monet resource lookup in `AppPalette.systemColor()`.

## 47C-6 SetTextI18n cleanup

- Moves the lint-reported `setText` concatenation call sites to string resources/plurals.
- Covers runner status, configuration issue text, repository picker status/count text, quick-tile two-line text, validation phase row text, validation dependency suffixes, and overlay ready text.

## 47C-7 Unused resource/style cleanup

- Removes unused `devtool_*` color resources that were no longer referenced.
- Removes unused non-notification `TextAppearance.Devtool.*` style resources.
- Deletes dead drawable resources: `ic_launcher_legacy.xml`, `ic_permissions.xml`, and `ic_start.xml`.

## Safety

No repo manager behavior, Git execution, bridge authentication, root/chroot executor planning, debug recording, or notification action semantics are changed.
