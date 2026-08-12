# Android NDK host fallback

Devtool can optionally repair Android native validation when the official Google
NDK host prebuilt is `linux-x86_64` but validation is running on ARM64 Linux,
Termux, or a chroot/proot environment.

## Providers

`ndk_host_provider` accepts:

- `auto` — on ARM hosts, prefer an existing/configured ARM64-host NDK first, then accept an already-runnable NDK, then bootstrap QEMU.
- `google` — require the official host NDK to run directly.
- `arm64` — require a configured ARM64-host NDK archive.
- `qemu` — wrap the official x86_64 NDK tools with `qemu-x86_64`.
- `none` — do not repair; warn only.


## Automatic order in `auto` mode

On ARM hosts, `auto` mode uses this order:

1. expose an already-installed ARM64-host NDK as the AGP-compatible `linux-x86_64` host tag;
2. download/extract the configured unofficial ARM64 NDK archive from `arm64_ndk_url` or `DEVTOOL_ARM64_NDK_URL`;
3. accept an already-runnable NDK, including a previously working wrapper;
4. bootstrap QEMU + amd64 sysroot and wrap the official Google x86_64 NDK tools.

The ARM64 NDK URL remains explicit because Google does not publish an official
Linux ARM64-host NDK package; community archives vary by maintainer/version.

## QEMU sysroot bootstrap

Installing `qemu-x86_64` alone is not enough on Termux. The official NDK
compiler also needs an x86_64 Linux dynamic loader and libc. When
`qemu_ld_prefix = "auto"`, devtool now searches for an existing x86_64 loader and,
if missing and downloads are allowed, bootstraps a small amd64 Ubuntu Base
sysroot under devtool's cache.

Default sysroot source:

```text
https://cdimages.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.4-base-amd64.tar.gz
```

Default SHA256:

```text
c1e67ef7b17a6300e136118bd1dc04725009cb376c1aad10abcf8cd453628d58
```

## Config

```toml
[android]
ndk_host_provider = "qemu"
qemu_install = "auto"
qemu_ld_prefix = "auto"
# Optional mirror / pinned rootfs:
qemu_sysroot_url = ""
qemu_sysroot_sha256 = ""
# Optional unofficial ARM64 NDK:
arm64_ndk_url = ""
```

## CLI

```bash
devtool -r ~/Code/dustui --target dustui_android validate \
  --ndk-host-provider qemu \
  --qemu-install auto \
  --qemu-ld-prefix auto
```

For a mirrored sysroot:

```bash
devtool -r ~/Code/dustui --target dustui_android validate \
  --ndk-host-provider qemu \
  --qemu-sysroot-url https://mirror.example/rootfs-amd64.tar.gz \
  --qemu-sysroot-sha256 <sha256>
```

## Chroot mode

The Android builder is staged into the chroot-visible devtool runtime path, so
these options work when validation is delegated through chroot mode as well.
If the chroot is already x86_64, the official Google NDK should run directly and
no QEMU wrapper is needed.
