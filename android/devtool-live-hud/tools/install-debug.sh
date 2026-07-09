#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
./gradlew :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
