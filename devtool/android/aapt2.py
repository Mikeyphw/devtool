"""AAPT2 and Android build-tools provider selection."""

from ._standalone import (
    AAPT2_OVERRIDE_PROPERTY,
    REVANCED_REPOSITORY,
    arm64_build_tools_replacements_needed,
    ensure_arm64_build_tools_compatible,
    native_android_build_tool_compatible,
    obtain_commit451_arm64_tool,
    obtain_revanced_aapt2,
    select_cached_revanced_aapt2,
    select_revanced_asset,
    select_sdk_build_tools_aapt2,
    select_termux_aapt2,
    stage_aapt2,
    termux_aapt2_path,
    termux_aapt2_supports_platform,
    validate_aapt2,
)

__all__ = [name for name in globals() if not name.startswith("_")]
