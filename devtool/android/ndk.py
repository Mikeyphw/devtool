"""Android NDK selection and host compatibility helpers."""

from ..android_build import (
    NDK_HOST_PROVIDERS,
    NdkAndroidLinkProbeResult,
    ensure_ndk_cmake_termux_host_support,
    ensure_ndk_empty_host_tag_compat,
    ensure_ndk_host_compatible,
    ensure_ndk_legacy_cmake_arm_alias,
    ensure_ndk_termux_multicall_aliases,
    ensure_native_android_ndk_layout,
    expose_arm64_ndk_as_linux_x86_64,
    find_arm64_ndk_prebuilt,
    format_ndk_link_probe_failure,
    installed_ndk_versions,
    native_android_ndk_prebuilt,
    ndk_clang_resource_dir,
    ndk_google_host_tag,
    ndk_google_tool_bin,
    ndk_host_android_link_probe,
    ndk_host_android_link_runs,
    ndk_host_linker_runs,
    ndk_host_resource_dir_ready,
    ndk_host_toolchain_runs,
    ndk_is_ready,
    ndk_prebuilt_root,
    ndk_root_for_version,
    obtain_unofficial_arm64_ndk,
    resolve_ndk_requirement,
    restore_google_ndk_x86_64_prebuilt_for_qemu,
    select_required_ndk_version,
)

__all__ = [name for name in globals() if not name.startswith("_")]
