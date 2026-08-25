from __future__ import annotations

from pathlib import Path
import shutil

from .api import PluginCheck, PluginContext, PluginDescriptor


def _tool(name: str, *, required: bool = True) -> PluginCheck:
    path = shutil.which(name)
    return PluginCheck(name, bool(path), path or "not found", required=required)


def _android_doctor(context: PluginContext) -> list[PluginCheck]:
    root = context.repo_root
    gradle = root / "gradlew"
    return [
        PluginCheck("gradle-wrapper", gradle.is_file(), str(gradle) if gradle.is_file() else "gradlew not found", required=False),
        _tool("java"),
        _tool("adb", required=False),
    ]


def _python_doctor(context: PluginContext) -> list[PluginCheck]:
    return [_tool("python3"), _tool("pip", required=False)]


def _rust_doctor(context: PluginContext) -> list[PluginCheck]:
    return [_tool("cargo"), _tool("rustc")]


def _magisk_doctor(context: PluginContext) -> list[PluginCheck]:
    root = context.repo_root
    module_prop = root / "module.prop"
    return [
        PluginCheck("module.prop", module_prop.is_file(), str(module_prop) if module_prop.is_file() else "module.prop not found", required=False),
        _tool("zip"),
    ]


BUILTIN_PLUGINS = (
    PluginDescriptor("android", "Android/Gradle build, SDK, NDK, device and instrumentation integration.", capabilities=("build", "test", "doctor", "artifacts"), doctor=_android_doctor),
    PluginDescriptor("python", "Python project validation, packaging, environment and source-audit integration.", capabilities=("build", "test", "lint", "package"), doctor=_python_doctor),
    PluginDescriptor("rust", "Rust/Cargo project integration.", capabilities=("build", "test", "lint"), doctor=_rust_doctor),
    PluginDescriptor("magisk", "Magisk/KernelSU module project integration hooks.", capabilities=("doctor", "package"), doctor=_magisk_doctor),
)
