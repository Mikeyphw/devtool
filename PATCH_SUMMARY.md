# Devtool Gradle and desktop UI fixes

## Gradle 9 structured-event arguments

- Whenever `devtool-events.init.gradle` is injected, DevTool also passes `--no-configuration-cache`.
- This prevents the structured-event init script from entering Gradle's configuration-cache path.
- The shared Gradle builder remains the single owner of the default `--console=plain` policy.
- Direct Gradle arguments are normalized so only one console option reaches Gradle.
- `--console value` and `--console=value` are both supported.
- When conflicting console values are supplied, the last explicit value wins.
- Added regression tests for the init-script/configuration-cache pairing and console deduplication.

## Desktop QML runtime fixes

- Replaced the nonexistent `theme.accentSurface` stage color with declared palette tokens.
- Moved History dialog accessibility metadata from `Popup` to each popup's `contentItem`.
- Added regression checks for undeclared theme tokens and invalid direct `Popup` accessibility attachments.

## Screenshot-driven layout fixes

### Run

- Bound workflow content to the actual `AppScrollView.availableWidth`.
- Rebuilt the step indicator as a responsive 4-column/2-column grid.
- Prevented clipped combo boxes, compressed fields, and overlapping stage labels.

### Artifacts

- Grouped artifact and repository fields in a labeled input card.
- Added a centered empty state with a primary open action.
- Prevented hidden detail layouts from reserving the page's vertical space.
- Kept inspected details in a bounded, fill-height area.

### Repositories

- “Current runs” now includes only runs whose status is actually `running`.
- Completed and failed runs remain in command/artifact history instead of appearing as 0% active work.

## Validation

- 465 Python tests passed.
- 28 subtests passed.
- Python compilation passed.
- Shell syntax validation passed.
- QML invalid-separator scan passed.
