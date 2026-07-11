# devtool 0.5.1

- Fix Devtool Control Center compilation with Qt 6.4.
- Include the complete `QVariantMap` type in `ProcessRunner.h`.
- Normalize `qsizetype` values to `int` in HUD step indexing.
- Replace invalid `QVariant::toString(default)` calls with explicit fallbacks.
- Load the QML entry point through its resource URL instead of the Qt 6.5-only `QQmlApplicationEngine::loadFromModule()` API.
- Pin the QML resource prefix so Qt 6.4 and newer resolve the same entry URL.
