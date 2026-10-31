#!/usr/bin/env bash
set -euo pipefail
SDK="${ANDROID_SDK_ROOT:-/opt/android-sdk}"
BT="$SDK/build-tools/36.0.0"
NDK="$SDK/ndk/29.0.14206865"
fail() { printf 'capability=%s\nstatus=failed\ndetail=%s\n' "$1" "$2"; exit "${3:-1}"; }
case "$(uname -m)" in aarch64|arm64) ;; *) fail architecture "expected aarch64/arm64, got $(uname -m)" 65;; esac
[ -f /usr/local/lib/devtool/oci-image-manifest.json ] || fail manifest 'image manifest is missing' 66
[ -f /usr/local/lib/devtool/oci-build-metadata.json ] || fail build-metadata 'image build metadata is missing' 66
[ -f /usr/local/lib/devtool/oci-immutable-toolchain ] || fail immutability 'immutable toolchain marker is missing' 66
[ -x /usr/local/lib/devtool/build-apk ] || fail devtool-runtime 'embedded Android builder is missing or not executable' 66
[ -f /usr/local/lib/devtool/termux_toolchains.py ] || fail devtool-runtime 'embedded toolchain companion is missing' 66
[ -f /usr/local/lib/devtool/devtool-events.init.gradle ] || fail devtool-runtime 'embedded Gradle telemetry init script is missing' 66
[ -f /usr/local/lib/devtool/devtool-events-full.init.gradle ] || fail devtool-runtime 'embedded Gradle full telemetry init script is missing' 66
echo 'db73ed326d4d5a774dcb079ea64e0df7325a2d2df43b2f2484216de6418126e8  /usr/local/lib/devtool/build-apk' | sha256sum -c - >/dev/null || fail devtool-runtime 'embedded Android builder digest mismatch' 65
echo '46bff66338eee52d297b90cbfe6773e979d9a27d0b3c93f7816a139cf0e255fe  /usr/local/lib/devtool/termux_toolchains.py' | sha256sum -c - >/dev/null || fail devtool-runtime 'embedded toolchain companion digest mismatch' 65
echo '9e0a0aa733ffeb41ce76e4b51b5bda4eadfcf52d01d49b3faf1dd4f33b2e72d8  /usr/local/lib/devtool/devtool-events.init.gradle' | sha256sum -c - >/dev/null || fail devtool-runtime 'embedded Gradle telemetry init digest mismatch' 65
echo '48529c7e3b7a2a4409aacbf102ffb47048165cd4badf3d73ba82e2b309c05788  /usr/local/lib/devtool/devtool-events-full.init.gradle' | sha256sum -c - >/dev/null || fail devtool-runtime 'embedded Gradle full telemetry init digest mismatch' 65
python3 -m py_compile /usr/local/lib/devtool/build-apk /usr/local/lib/devtool/termux_toolchains.py || fail devtool-runtime 'embedded Python runtime resources do not compile' 65
for c in bash git rsync ssh tmux python3 java node npm cmake ninja clang file sha256sum; do command -v "$c" >/dev/null 2>&1 || fail "command:$c" "$c is missing" 127; done
py="$(python3 -c 'import platform; print(platform.python_version())')"
case "$py" in 3.12.*) ;; *) fail python "expected pinned Python 3.12.x, got $py" 65;; esac
python3 -c 'import rich, importlib.metadata; assert tuple(map(int, importlib.metadata.version("rich").split(".")[:2])) >= (13,7)' || fail python:rich 'rich>=13.7 is not importable' 65
node="$(node --version 2>/dev/null || true)"; [ "$node" = v22.20.0 ] || fail node "expected v22.20.0, got ${node:-missing}" 65
for j in 17 21; do
  bin="/usr/lib/jvm/java-${j}-openjdk-arm64/bin/java"
  [ -x "$bin" ] || fail "jdk:$j" "missing $bin" 65
  "$bin" -version >/dev/null 2>&1 || fail "jdk:$j" "JDK $j is not runnable" 65
done
java -version 2>&1 | head -n1 | grep -Eq 'version "21[.]' || fail jdk:default 'default java is not JDK 21' 65
[ -f "$SDK/platforms/android-36/android.jar" ] || fail android-platform 'platforms;android-36 is missing' 65
[ -d "$BT" ] || fail android-build-tools 'build-tools;36.0.0 is missing' 65
[ -d "$NDK" ] || fail android-ndk 'NDK 29.0.14206865 is missing' 65
for t in aapt2 aidl zipalign split-select; do
  p="$BT/$t"; [ -x "$p" ] || fail "android-tool:$t" "$p is missing or not executable" 65
  desc="$(file -b "$p" 2>/dev/null || true)"; printf '%s' "$desc" | grep -Eqi 'aarch64|ARM aarch64' || fail "android-tool:$t" "$p is not an ARM64 ELF: $desc" 65
  rc=0; timeout 10 "$p" --help >/dev/null 2>&1 || rc=$?; [ "$rc" -lt 126 ] || fail "android-tool:$t" "$p is not runnable (exit $rc)" 65
done
[ -x "$BT/apksigner" ] || fail android-tool:apksigner 'apksigner is missing' 65
"$BT/apksigner" --version >/dev/null 2>&1 || fail android-tool:apksigner 'apksigner is not runnable' 65
command -v gradle >/dev/null 2>&1 && fail gradle-policy 'system Gradle distribution is baked into image; project wrapper must remain authoritative' 65
qemu="$(command -v qemu-x86_64-static 2>/dev/null || command -v qemu-x86_64 2>/dev/null || true)"; [ -n "$qemu" ] || fail android-ndk-host 'qemu x86_64 fallback is unavailable for the official Linux NDK host tools' 65
ndk_bin="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
ndk_clang="$ndk_bin/aarch64-linux-android21-clang"
[ -x "$ndk_clang" ] || fail android-ndk-host "official NDK Android clang driver is missing: $ndk_clang" 65
[ -x "$ndk_bin/clang.x86_64" ] || fail android-ndk-host 'NDK clang was not preserved behind the QEMU wrapper' 65
head -n1 "$ndk_bin/clang" | grep -q '^#!/bin/sh' || fail android-ndk-host 'NDK clang is not transparently wrapped for ARM64' 65
probe="$(mktemp -d)"; trap 'rm -rf "$probe"' EXIT HUP INT TERM
printf 'int main(void){return 0;}\n' > "$probe/main.c"
timeout 45 "$ndk_clang" "$probe/main.c" -o "$probe/android-probe" >/dev/null 2>&1 || fail android-ndk-host 'official NDK Android clang could not compile+link through transparent QEMU wrappers' 65
file -b "$probe/android-probe" | grep -Eqi 'aarch64|ARM aarch64' || fail android-ndk-host 'NDK smoke output is not Android ARM64' 65
rm -rf "$probe"; trap - EXIT HUP INT TERM
printf 'capability=android-oci\nstatus=ready\narchitecture=%s\npython=%s\nnode=%s\nsdk_root=%s\napi=36\nbuild_tools=36.0.0\nndk=29.0.14206865\nndk_host=qemu-x86_64-wrapped-verified\nqemu=%s\n' "$(uname -m)" "$py" "$node" "$SDK" "$qemu"
