from __future__ import annotations

import importlib
from pathlib import Path

import devtool.android_build as legacy
from devtool.android import aapt2, build, cmake, diagnostics, gradle, ndk, sdk, tasks
from devtool.android.providers import linux, qemu, termux


def test_domain_modules_preserve_legacy_builder_contract() -> None:
    assert build.main is legacy.main
    assert build.standalone_main is legacy.standalone_main
    assert gradle.select_gradle is legacy.select_gradle
    assert sdk.ensure_android_sdk is legacy.ensure_android_sdk
    assert ndk.ensure_ndk_host_compatible is legacy.ensure_ndk_host_compatible
    assert cmake.resolve_cmake_requirement is legacy.resolve_cmake_requirement
    assert aapt2.validate_aapt2 is legacy.validate_aapt2
    assert tasks.is_connected_android_test_task is legacy.is_connected_android_test_task


def test_provider_boundaries_are_importable() -> None:
    assert termux.running_under_termux_python is legacy.running_under_termux_python
    assert qemu.find_qemu_x86_64 is legacy.find_qemu_x86_64
    assert callable(linux.is_linux_host)


def test_android_artifacts_uses_modular_build_surface() -> None:
    source = Path(importlib.import_module("devtool.android_artifacts").__file__).read_text(encoding="utf-8")
    assert "from .android.build import" in source
    assert "from .android_build import" not in source


def test_bootstrap_uses_provider_surface() -> None:
    source = Path(importlib.import_module("devtool.bootstrap").__file__).read_text(encoding="utf-8")
    assert "from .android.providers.termux import" in source
    assert "from .android_build import" not in source


def test_standalone_builder_remains_package_independent_entrypoint() -> None:
    # Chroot staging still copies the self-contained legacy builder; the modular
    # package is for in-process consumers and must not silently change this ABI.
    integration = Path(importlib.import_module("devtool.android_integration").__file__).read_text(encoding="utf-8")
    assert "from . import android_build" in integration


def test_diagnostic_snapshot_serializes_minimal_gradle_project(tmp_path: Path) -> None:
    (tmp_path / "settings.gradle").write_text('rootProject.name = "sample"\n', encoding="utf-8")
    (tmp_path / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
    snapshot = diagnostics.inspect_android_project(tmp_path)
    payload = snapshot.as_dict()
    assert payload["root"] == str(tmp_path.resolve())
    assert payload["gradle"] is True
    assert payload["display_name"]
