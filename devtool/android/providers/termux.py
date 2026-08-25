"""Termux-native Android provider API."""

from ...android_build import (
    ensure_termux_android_tool_packages,
    ensure_termuxvoid_repository,
    native_android_build_tool_compatible,
    running_under_termux_python,
    termux_aapt2_path,
    termux_aapt2_supports_platform,
    termux_can_repair_sdk_build_tools,
    termux_ndk_sysroot_ready,
    termux_package_version,
    termux_prefix_path,
    termux_tool_path,
)
from ...termux_toolchains import (
    NativeRequirement,
    ProviderDecisionEvidence,
    RuntimeIdentity,
    ToolProvider,
    classify_native_requirement,
    detect_runtime_identity,
    resolve_provider_decisions,
    termux_android_tool_providers,
    termux_ccache_probe,
    termux_cmake_probe,
    termux_gradle_probe,
    termux_java_providers,
    termux_native_provider,
    termux_ninja_probe,
    termux_package_probe,
    termux_prefix,
)

__all__ = [name for name in globals() if not name.startswith("_")]
