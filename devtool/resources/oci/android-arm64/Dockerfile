FROM ubuntu:24.04
ARG DEBIAN_FRONTEND=noninteractive
ARG DEVTOOL_APT_SNAPSHOT=20260911T235959Z
ARG DEVTOOL_DEFINITION_DIGEST=unknown
ARG DEVTOOL_BUILD_CONTEXT_SHA256=unknown
ENV ANDROID_SDK_ROOT=/opt/android-sdk ANDROID_HOME=/opt/android-sdk JAVA_HOME=/usr/lib/jvm/java-21-openjdk-arm64 DEVTOOL_OCI_IMMUTABLE=1
RUN set -eux; \
    dpkg --add-architecture amd64 \
 && rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*.sources \
 && printf '%s\n' \
      'Types: deb' \
      'URIs: http://ports.ubuntu.com/ubuntu-ports' \
      'Suites: noble noble-updates noble-backports' \
      'Components: main restricted universe multiverse' \
      'Architectures: arm64' \
      'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' \
      '' \
      'Types: deb' \
      'URIs: http://ports.ubuntu.com/ubuntu-ports' \
      'Suites: noble-security' \
      'Components: main restricted universe multiverse' \
      'Architectures: arm64' \
      'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' \
      > /etc/apt/sources.list.d/devtool-arm64.sources \
 && printf '%s\n' \
      'Types: deb' \
      'URIs: http://archive.ubuntu.com/ubuntu' \
      'Suites: noble noble-updates noble-backports' \
      'Components: main restricted universe multiverse' \
      'Architectures: amd64' \
      'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' \
      '' \
      'Types: deb' \
      'URIs: http://security.ubuntu.com/ubuntu' \
      'Suites: noble-security' \
      'Components: main restricted universe multiverse' \
      'Architectures: amd64' \
      'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' \
      > /etc/apt/sources.list.d/devtool-amd64.sources \
 && printf 'APT::Snapshot "%s";\n' "$DEVTOOL_APT_SNAPSHOT" > /etc/apt/apt.conf.d/50devtool-snapshot \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      bash build-essential ca-certificates ccache clang cmake coreutils curl file git grep jq lld ninja-build \
      openjdk-17-jdk-headless openjdk-21-jdk-headless openssh-client pkg-config python3 python3-pip \
      python3-rich python3-venv qemu-user-static rsync tmux unzip xz-utils zip \
      libc6:amd64 libgcc-s1:amd64 libstdc++6:amd64 zlib1g:amd64 \
 && update-alternatives --set java /usr/lib/jvm/java-21-openjdk-arm64/bin/java \
 && update-alternatives --set javac /usr/lib/jvm/java-21-openjdk-arm64/bin/javac \
 && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
  curl -fsSLo /tmp/node.tar.xz https://nodejs.org/download/release/v22.20.0/node-v22.20.0-linux-arm64.tar.xz; \
  echo '06907b9c088ce62305bc1530e5c1ae1510245114645768f7750c349c5b6fe667  /tmp/node.tar.xz' | sha256sum -c -; \
  tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1; rm /tmp/node.tar.xz; node --version
RUN set -eux; mkdir -p "$ANDROID_SDK_ROOT/cmdline-tools"; \
  curl -fsSLo /tmp/cmdline.zip https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip; \
  echo '4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583  /tmp/cmdline.zip' | sha256sum -c -; \
  unzip -q /tmp/cmdline.zip -d /tmp/android-cli; mv /tmp/android-cli/cmdline-tools "$ANDROID_SDK_ROOT/cmdline-tools/latest"; rm -rf /tmp/cmdline.zip /tmp/android-cli; \
  yes | "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager" --licenses >/dev/null; \
  "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager" 'platforms;android-36' 'build-tools;36.0.0' 'ndk;29.0.14206865'; \
  test -f "$ANDROID_SDK_ROOT/platforms/android-36/android.jar"
# The official Linux NDK host prebuilt is x86_64. Keep Google's exact NDK and
# make its real ELF executables transparent on ARM64 by wrapping each regular
# x86_64 binary through qemu-user. -0 preserves argv[0], which matters for
# multi-call drivers such as clang/clang++. Symlinks remain pointed at wrappers.
RUN set -eux; \
  ndk_bin="$ANDROID_SDK_ROOT/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/bin"; \
  qemu="$(command -v qemu-x86_64-static 2>/dev/null || command -v qemu-x86_64 2>/dev/null || true)"; \
  test -n "$qemu"; wrapped=0; \
  for p in "$ndk_bin"/*; do \
    [ -f "$p" ] && [ -x "$p" ] && [ ! -L "$p" ] || continue; \
    file -b "$p" | grep -Eqi 'x86[-_]64|x86-64' || continue; \
    mv "$p" "$p.x86_64"; \
    printf '#!/bin/sh\nexec %s -0 "$0" %s "$@"\n' "$qemu" "$p.x86_64" > "$p"; \
    chmod 0755 "$p"; wrapped=$((wrapped + 1)); \
  done; \
  test "$wrapped" -gt 0; \
  "$ndk_bin/aarch64-linux-android21-clang" --version
# Google's Linux build-tools package is x86_64-only. Replace only the four native
# build-path tools with matching AOSP Android-16/36.0.0 ARM64 release binaries;
# retain Google's architecture-independent apksigner/d8 jars and scripts.
RUN set -eux; \
  base='https://github.com/Commit451/android-arm-build-tools/releases/download/platform-tools-36.0.0'; \
  cd "$ANDROID_SDK_ROOT/build-tools/36.0.0"; \
  curl -fsSLo aapt2.arm64 "$base/aapt2"; echo '7512ff7e381bea6fd310b6f6e347422c8fda21e07c6e3f0162742e95eb9d7f98  aapt2.arm64' | sha256sum -c -; \
  curl -fsSLo aidl.arm64 "$base/aidl"; echo '8c97356b8bba8f7aad44cfd408e3ee24c66a1258244b5d08af1c9249f50dd659  aidl.arm64' | sha256sum -c -; \
  curl -fsSLo zipalign.arm64 "$base/zipalign"; echo 'e8856fb24b10095eb6e940c577ce96d89ddbfc45aa0f7eeaef1597ef68f11a12  zipalign.arm64' | sha256sum -c -; \
  curl -fsSLo split-select.arm64 "$base/split-select"; echo 'fb7f0c3c87dbd4243d7e1277ba64ded2399389244058b6a4c969cc615360766c  split-select.arm64' | sha256sum -c -; \
  for t in aapt2 aidl zipalign split-select; do chmod 0755 "$t.arm64"; mv -f "$t.arm64" "$t"; done; \
  "$ANDROID_SDK_ROOT/build-tools/36.0.0/aapt2" version
COPY image-manifest.json /usr/local/lib/devtool/oci-image-manifest.json
COPY validate-image.sh /usr/local/lib/devtool/validate-android-oci.sh
COPY build-apk /usr/local/lib/devtool/build-apk
COPY termux_toolchains.py /usr/local/lib/devtool/termux_toolchains.py
COPY devtool-events.init.gradle /usr/local/lib/devtool/devtool-events.init.gradle
RUN chmod 0555 /usr/local/lib/devtool/validate-android-oci.sh /usr/local/lib/devtool/build-apk \
 && : > /usr/local/lib/devtool/oci-immutable-toolchain \
 && mkdir -p /root/.gradle \
 && printf 'android.aapt2FromMavenOverride=/opt/android-sdk/build-tools/36.0.0/aapt2\n' > /root/.gradle/gradle.properties \
 && DEVTOOL_DEFINITION_DIGEST="$DEVTOOL_DEFINITION_DIGEST" DEVTOOL_BUILD_CONTEXT_SHA256="$DEVTOOL_BUILD_CONTEXT_SHA256" DEVTOOL_APT_SNAPSHOT="$DEVTOOL_APT_SNAPSHOT" python3 -c 'import hashlib,json,os,platform,subprocess; pkg=lambda n: subprocess.check_output(["dpkg-query","-W","-f=${Version}",n],text=True).strip(); sha=lambda p: hashlib.sha256(open(p,"rb").read()).hexdigest(); bt="/opt/android-sdk/build-tools/36.0.0"; data={"schema_version":1,"definition_digest":os.environ["DEVTOOL_DEFINITION_DIGEST"],"context_sha256":os.environ["DEVTOOL_BUILD_CONTEXT_SHA256"],"apt_snapshot":os.environ["DEVTOOL_APT_SNAPSHOT"],"architecture":platform.machine(),"toolchains":{"python":platform.python_version(),"node":subprocess.check_output(["node","--version"],text=True).strip(),"jdk_default":subprocess.run(["java","-version"],text=True,capture_output=True).stderr.splitlines()[0],"android_api":36,"build_tools":"36.0.0","ndk":"29.0.14206865","ndk_host_mode":"qemu-x86_64-wrapped","ndk_wrapped_host_binaries":len(__import__("glob").glob("/opt/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/bin/*.x86_64")),"arm64_build_tools_sha256":{n:sha(bt+"/"+n) for n in ("aapt2","aidl","zipalign","split-select")}},"packages":{n:pkg(n) for n in ("openjdk-17-jdk-headless","openjdk-21-jdk-headless","python3","python3-rich","qemu-user-static","libc6:amd64","libstdc++6:amd64")}}; open("/usr/local/lib/devtool/oci-build-metadata.json","w").write(json.dumps(data,sort_keys=True,separators=(",",":"))+"\\n")' \
 && /usr/local/lib/devtool/validate-android-oci.sh
LABEL org.opencontainers.image.title="Devtool Android ARM64 build environment" \
      io.devtool.image.definition="android-arm64-api36-v2" \
      io.devtool.android.api="36" \
      io.devtool.android.build-tools="36.0.0" \
      io.devtool.android.ndk="29.0.14206865" \
      io.devtool.gradle.policy="project-wrapper-first"
WORKDIR /workspace
CMD ["/bin/bash"]
