#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
APP_DIR="${APP_DIR:-$SCRIPT_DIR}"
SDK_DIR="${ANDROID_HOME:-$HOME/android-sdk}"
CMDLINE_ZIP="${CMDLINE_ZIP:-commandlinetools-linux-13114758_latest.zip}"
if [[ -n "${CMDLINE_URL+x}" ]]; then
  CMDLINE_URL_EXPLICIT=true
  CMDLINE_URL_VALUE="$CMDLINE_URL"
else
  CMDLINE_URL_EXPLICIT=false
  CMDLINE_URL_VALUE="https://dl.google.com/android/repository/$CMDLINE_ZIP"
fi
JAVA_HOME_VALUE="${JAVA_HOME:-/usr/lib/jvm/java-21-openjdk-amd64}"
ANDROID_PLATFORM="${ANDROID_PLATFORM:-android-36}"
ANDROID_BUILD_TOOLS="${ANDROID_BUILD_TOOLS:-36.0.0}"
PLATFORM_TOOLS_PACKAGE="${PLATFORM_TOOLS_PACKAGE:-platform-tools}"
GRADLE_TRANSFORMS_DIR="${GRADLE_TRANSFORMS_DIR:-$HOME/.gradle/caches/9.7.1/transforms}"
GRADLE_CLEAN_TASK="${GRADLE_CLEAN_TASK:-clean}"
GRADLE_ASSEMBLE_TASK="${GRADLE_ASSEMBLE_TASK:-assembleDebug}"
APK_PATH="${APK_PATH:-}"
TRACKED_APK_PATH="${TRACKED_APK_PATH:-}"
APT_COMMAND="${APT_COMMAND:-apt}"
SUDO_COMMAND="${SUDO_COMMAND:-sudo}"
WGET_COMMAND="${WGET_COMMAND:-wget}"
UNZIP_COMMAND="${UNZIP_COMMAND:-unzip}"
SDKMANAGER_COMMAND="${SDKMANAGER_COMMAND:-sdkmanager}"
APT_PACKAGES=(openjdk-21-jdk unzip wget)
GRADLE_ARGS=(--no-daemon)
SKIP_SYSTEM_DEPS=false
SKIP_SDK_INSTALL=false
SKIP_LICENSES=false
STOP_GRADLE=true
CLEAN_BUILD_DIRS=true
CLEAN_GRADLE_TRANSFORMS=true
RUN_CLEAN_TASK=true
DRY_RUN=false
VERIFY_SIGNATURE=false

usage() {
  cat <<'USAGE'
Usage: build.sh [options]

Build Devtool Live HUD on Ubuntu amd64. Every original default can be
overridden with a command-line flag; environment variables remain supported.

Paths and tools:
  --app-dir PATH               App source directory
                               default: $HOME/Code/devtool-live-hud
  --sdk-dir PATH               Android SDK directory
                               default: $ANDROID_HOME or $HOME/android-sdk
  --cmdline-zip NAME           Android command-line tools archive name
  --cmdline-url URL            Command-line tools download URL
  --java-home PATH             JAVA_HOME
  --apt-command COMMAND        apt command (default: apt)
  --sudo-command COMMAND       privilege command (default: sudo; use '' for none)
  --wget-command COMMAND       downloader (default: wget)
  --unzip-command COMMAND      unzip program (default: unzip)
  --sdkmanager-command CMD     sdkmanager command after PATH setup

SDK packages:
  --platform ID                Android platform ID (default: android-36)
  --build-tools VERSION        Build-tools version (default: 36.0.0)
  --platform-tools-package ID  Platform-tools package (default: platform-tools)
  --apt-packages "A B ..."     Replace the default apt package list
  --apt-package NAME           Append one apt package; may be repeated

Gradle and output:
  --clean-task TASK            Gradle clean task (default: clean)
  --assemble-task TASK         Gradle assemble task (default: assembleDebug)
  --gradle-arg ARG             Additional Gradle argument; may be repeated
  --gradle-transforms-dir PATH Cache directory removed before build
  --apk-path PATH              Expected Gradle APK path; default is inferred
  --tracked-apk PATH           Copy the built APK to this stable tracked path
                               default: debug/release filename selected by task
  --verify-signature           Verify the APK with build-tools apksigner

Behavior:
  --skip-system-deps           Do not run apt update/install
  --skip-sdk-install           Do not install SDK packages/tools
  --skip-licenses              Do not accept SDK licenses
  --no-gradle-stop             Do not run ./gradlew --stop
  --no-clean-build-dirs        Keep build and app/build
  --no-clean-gradle-transforms Keep the configured transforms cache
  --no-clean-task              Skip the Gradle clean task
  --gradle-daemon              Remove the default --no-daemon argument
  --no-sudo                    Equivalent to --sudo-command ''
  --dry-run                    Print commands without executing them
  -h, --help                   Show this help

Environment equivalents:
  APP_DIR, ANDROID_HOME, CMDLINE_ZIP, CMDLINE_URL, JAVA_HOME,
  ANDROID_PLATFORM, ANDROID_BUILD_TOOLS, PLATFORM_TOOLS_PACKAGE,
  GRADLE_TRANSFORMS_DIR, GRADLE_CLEAN_TASK, GRADLE_ASSEMBLE_TASK, APK_PATH,
  TRACKED_APK_PATH,
  APT_COMMAND, SUDO_COMMAND, WGET_COMMAND, UNZIP_COMMAND, SDKMANAGER_COMMAND.
USAGE
}

need_value() {
  [[ $# -ge 2 && -n "${2:-}" ]] || { echo "Missing value for $1" >&2; exit 2; }
}

while (($#)); do
  case "$1" in
    --app-dir) need_value "$@"; APP_DIR="$2"; shift 2 ;;
    --sdk-dir|--android-home) need_value "$@"; SDK_DIR="$2"; shift 2 ;;
    --cmdline-zip) need_value "$@"; CMDLINE_ZIP="$2"; shift 2 ;;
    --cmdline-url) need_value "$@"; CMDLINE_URL_VALUE="$2"; CMDLINE_URL_EXPLICIT=true; shift 2 ;;
    --java-home) need_value "$@"; JAVA_HOME_VALUE="$2"; shift 2 ;;
    --platform) need_value "$@"; ANDROID_PLATFORM="$2"; shift 2 ;;
    --build-tools) need_value "$@"; ANDROID_BUILD_TOOLS="$2"; shift 2 ;;
    --platform-tools-package) need_value "$@"; PLATFORM_TOOLS_PACKAGE="$2"; shift 2 ;;
    --apt-command) need_value "$@"; APT_COMMAND="$2"; shift 2 ;;
    --sudo-command) [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }; SUDO_COMMAND="$2"; shift 2 ;;
    --no-sudo) SUDO_COMMAND=""; shift ;;
    --wget-command) need_value "$@"; WGET_COMMAND="$2"; shift 2 ;;
    --unzip-command) need_value "$@"; UNZIP_COMMAND="$2"; shift 2 ;;
    --sdkmanager-command) need_value "$@"; SDKMANAGER_COMMAND="$2"; shift 2 ;;
    --apt-packages) need_value "$@"; read -r -a APT_PACKAGES <<<"$2"; shift 2 ;;
    --apt-package) need_value "$@"; APT_PACKAGES+=("$2"); shift 2 ;;
    --clean-task) need_value "$@"; GRADLE_CLEAN_TASK="$2"; shift 2 ;;
    --assemble-task) need_value "$@"; GRADLE_ASSEMBLE_TASK="$2"; shift 2 ;;
    --gradle-arg) need_value "$@"; GRADLE_ARGS+=("$2"); shift 2 ;;
    --gradle-transforms-dir) need_value "$@"; GRADLE_TRANSFORMS_DIR="$2"; shift 2 ;;
    --apk-path) need_value "$@"; APK_PATH="$2"; shift 2 ;;
    --tracked-apk) need_value "$@"; TRACKED_APK_PATH="$2"; shift 2 ;;
    --verify-signature) VERIFY_SIGNATURE=true; shift ;;
    --skip-system-deps) SKIP_SYSTEM_DEPS=true; shift ;;
    --skip-sdk-install) SKIP_SDK_INSTALL=true; shift ;;
    --skip-licenses) SKIP_LICENSES=true; shift ;;
    --no-gradle-stop) STOP_GRADLE=false; shift ;;
    --no-clean-build-dirs) CLEAN_BUILD_DIRS=false; shift ;;
    --no-clean-gradle-transforms) CLEAN_GRADLE_TRANSFORMS=false; shift ;;
    --no-clean-task) RUN_CLEAN_TASK=false; shift ;;
    --gradle-daemon)
      filtered=()
      for arg in "${GRADLE_ARGS[@]}"; do [[ "$arg" == "--no-daemon" ]] || filtered+=("$arg"); done
      GRADLE_ARGS=("${filtered[@]}")
      shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; GRADLE_ARGS+=("$@"); break ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! "$CMDLINE_URL_EXPLICIT"; then
  CMDLINE_URL_VALUE="https://dl.google.com/android/repository/$CMDLINE_ZIP"
fi

run() {
  printf '+ '
  printf '%q ' "$@"
  printf '\n'
  "$DRY_RUN" || "$@"
}

run_shell() {
  printf '+ %s\n' "$1"
  "$DRY_RUN" || bash -lc "$1"
}

if [[ -z "$TRACKED_APK_PATH" ]]; then
  case "$GRADLE_ASSEMBLE_TASK" in
    *Release*) TRACKED_APK_PATH="$APP_DIR/devtool-live-hud-release.apk" ; VERIFY_SIGNATURE=true ;;
    *) TRACKED_APK_PATH="$APP_DIR/devtool-live-hud-debug.apk" ;;
  esac
fi

if [[ -z "$APK_PATH" ]]; then
  case "$GRADLE_ASSEMBLE_TASK" in
    *Release*) APK_PATH="$APP_DIR/app/build/outputs/apk/release/app-release.apk" ;;
    *) APK_PATH="$APP_DIR/app/build/outputs/apk/debug/app-debug.apk" ;;
  esac
fi

printf '== Ubuntu amd64 Android build ==\n'
printf 'App:          %s\n' "$APP_DIR"
printf 'Android SDK:  %s\n' "$SDK_DIR"
printf 'Platform:     %s\n' "$ANDROID_PLATFORM"
printf 'Build tools:  %s\n' "$ANDROID_BUILD_TOOLS"
printf 'Gradle task:  %s\n' "$GRADLE_ASSEMBLE_TASK"

# Validate the requested app tree before mutating host packages, SDK folders,
# licenses, Gradle caches, or tracked APK outputs.
[[ -d "$APP_DIR" ]] || { echo "App directory not found: $APP_DIR" >&2; exit 2; }
[[ -x "$APP_DIR/gradlew" || -f "$APP_DIR/gradlew" ]] || { echo "Gradle wrapper is missing: $APP_DIR/gradlew" >&2; exit 2; }
[[ -f "$APP_DIR/settings.gradle" || -f "$APP_DIR/settings.gradle.kts" ]] || { echo "Gradle settings file is missing under: $APP_DIR" >&2; exit 2; }

if ! "$SKIP_SYSTEM_DEPS"; then
  prefix=()
  [[ -z "$SUDO_COMMAND" ]] || read -r -a prefix <<<"$SUDO_COMMAND"
  run "${prefix[@]}" "$APT_COMMAND" update
  run "${prefix[@]}" "$APT_COMMAND" install -y "${APT_PACKAGES[@]}"
fi

export JAVA_HOME="$JAVA_HOME_VALUE"
export ANDROID_HOME="$SDK_DIR"
export ANDROID_SDK_ROOT="$SDK_DIR"
export PATH="$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"

if ! "$SKIP_SDK_INSTALL"; then
  run mkdir -p "$ANDROID_HOME/cmdline-tools"
  if [[ ! -x "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" ]]; then
    run rm -rf "$ANDROID_HOME/cmdline-tools/latest" "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/$CMDLINE_ZIP"
    run "$WGET_COMMAND" -O "$ANDROID_HOME/cmdline-tools/$CMDLINE_ZIP" "$CMDLINE_URL_VALUE"
    run "$UNZIP_COMMAND" -q "$ANDROID_HOME/cmdline-tools/$CMDLINE_ZIP" -d "$ANDROID_HOME/cmdline-tools"
    run mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest"
  fi

  SDKMANAGER_BIN="$SDKMANAGER_COMMAND"
  if [[ "$SDKMANAGER_COMMAND" == "sdkmanager" ]]; then
    SDKMANAGER_BIN="$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager"
  fi
  if ! "$SKIP_LICENSES"; then
    if "$DRY_RUN"; then
      echo "+ yes | $SDKMANAGER_BIN --licenses >/dev/null || true"
    else
      yes | "$SDKMANAGER_BIN" --licenses >/dev/null || true
    fi
  fi
  run "$SDKMANAGER_BIN" \
    "$PLATFORM_TOOLS_PACKAGE" \
    "platforms;$ANDROID_PLATFORM" \
    "build-tools;$ANDROID_BUILD_TOOLS"
fi

cd "$APP_DIR"
run chmod +x ./gradlew
"$STOP_GRADLE" && { run ./gradlew --stop || true; }
"$CLEAN_BUILD_DIRS" && run rm -rf build app/build
"$CLEAN_GRADLE_TRANSFORMS" && run rm -rf "$GRADLE_TRANSFORMS_DIR"
"$RUN_CLEAN_TASK" && run ./gradlew "$GRADLE_CLEAN_TASK" "${GRADLE_ARGS[@]}"
run ./gradlew "$GRADLE_ASSEMBLE_TASK" "${GRADLE_ARGS[@]}"

if ! "$DRY_RUN"; then
  [[ -s "$APK_PATH" ]] || { echo "APK was not produced or is empty: $APK_PATH" >&2; exit 1; }
  if "$VERIFY_SIGNATURE"; then
    apksigner="$ANDROID_HOME/build-tools/$ANDROID_BUILD_TOOLS/apksigner"
    [[ -x "$apksigner" ]] || { echo "apksigner is unavailable: $apksigner" >&2; exit 1; }
    "$apksigner" verify --verbose --print-certs "$APK_PATH"
  fi
  mkdir -p "$(dirname "$TRACKED_APK_PATH")"
  cp -f -- "$APK_PATH" "$TRACKED_APK_PATH"
  sha256sum "$TRACKED_APK_PATH" > "$TRACKED_APK_PATH.sha256"
fi
if "$DRY_RUN"; then
  printf '\nDRY RUN COMPLETE - no APK was built or copied.\n'
  printf 'Planned Gradle APK: %s\nPlanned tracked APK: %s\nPlanned checksum: %s.sha256\n' "$APK_PATH" "$TRACKED_APK_PATH" "$TRACKED_APK_PATH"
else
  printf '\nBUILD SUCCESS\nGradle APK: %s\nTracked APK: %s\nChecksum: %s.sha256\n' "$APK_PATH" "$TRACKED_APK_PATH" "$TRACKED_APK_PATH"
fi
