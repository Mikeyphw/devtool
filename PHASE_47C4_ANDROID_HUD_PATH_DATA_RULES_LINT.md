# Phase 47C-4: Android HUD path lint + data extraction rules

This slice cleans the next security/privacy lint bucket after 47C-2/47C-3.

## Changes

- Adds local `@SuppressLint("SdCardPath")` annotations to Android HUD classes that intentionally reason about Termux, root, chroot, `/data`, `/sdcard`, and `/storage` paths.
- Keeps those paths explicit because replacing them with `Context.getFilesDir()` would break Termux/root namespace diagnostics.
- Adds `android:dataExtractionRules="@xml/data_extraction_rules"` while keeping `android:allowBackup="false"`.
- Adds data extraction rules that exclude files, databases, shared preferences, and external debug evidence from cloud backup and device transfer.

## Safety

No Git behavior, root/chroot execution, bridge authentication, repository repair, or debug recorder semantics changed.
