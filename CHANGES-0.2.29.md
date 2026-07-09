# Devtool 0.2.29 changes

## Android frontend 1.4.0

- Rebuilt the launcher around the portrait-first mock: compact page headers, single-column cards, state-driven setup tiles, and persistent bottom navigation.
- Permission and integration controls now react to live state. Actions change between Grant access, Manage, Enable, Disable, Install token, Update token, and Verify.
- Added one-tap secure bridge pairing. The app writes `~/.config/devtool/android-live-hud.token` through Termux RunCommandService, sets mode 600, verifies the value, and reports Connected/Update needed/error states.
- Kept manual token copy and regeneration under Secure bridge for recovery and advanced use.
- Preserved GTK colors by default and optional Android Monet colors.

## HUD correctness and noise reduction

- Desktop HUD event lists are filtered by the current run ID, preventing previous-run failures and warnings from leaking into a new run.
- Successful apply rollback is presented as recovery (`Changes rolled back`) rather than a failed rollback phase.
- Final failed state retains the original failing phase instead of inheriting `apply-rollback`.
- Error banners now prefer actionable failures, suppress duplicate generic completion errors, cap visible error lines, and collapse duplicate recent events.
- Completion payloads expose rollback outcome and report concise recovery headlines.

## Validation sequencing

- .NET validation always runs build with `--no-restore` after restore.
- .NET validation always runs test with `--no-build` after build.
- The flags are now also injected into explicit `build_command` and `test_command` definitions when they are recognizable `dotnet build`/`dotnet test` commands.

## Validation

- 235 Python tests passed.
- 22 Android Java source files passed parser validation.
- 14 Android XML resources/manifests parsed successfully.
- Shell entry points passed syntax validation.
- A full Android Gradle build was attempted but could not download Android command-line tools because this environment has no DNS/network access.
