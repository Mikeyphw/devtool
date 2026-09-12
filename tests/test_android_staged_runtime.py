from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import devtool.android_build as android_build

from devtool.android_integration import (
    refresh_android_builders_for_specs,
    stage_android_builder,
)
from devtool.models import CommandSpec


def test_staged_builder_runtime_bundle_is_executable_and_has_companion() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "build-apk"

        staged = stage_android_builder(destination=target)
        companion = staged.with_name("termux_toolchains.py")
        text = staged.read_text(encoding="utf-8")

        assert os.access(staged, os.X_OK)
        assert companion.is_file()

        assert "from devtool" not in text
        assert "from .termux_toolchains" not in text


def test_staged_builder_inspects_project_without_python_package_context() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        runtime = root / "runtime"
        project = root / "project"

        runtime.mkdir()
        project.mkdir()

        (project / "settings.gradle").write_text(
            "rootProject.name = 'standalone-fixture'\n",
            encoding="utf-8",
        )

        (project / "build.gradle").write_text(
            "plugins { id 'com.android.application' version '8.8.0' apply false }\n"
            "android { compileSdk 36 }\n",
            encoding="utf-8",
        )

        staged = stage_android_builder(
            destination=runtime / "build-apk"
        )

        probe = (
            "import importlib.machinery, importlib.util, pathlib, sys; "
            "loader=importlib.machinery.SourceFileLoader("
            "'standalone_android_builder', sys.argv[1]); "
            "spec=importlib.util.spec_from_loader(loader.name, loader); "
            "m=importlib.util.module_from_spec(spec); "
            "sys.modules[spec.name]=m; "
            "loader.exec_module(m); "
            "info=m.inspect_project(pathlib.Path(sys.argv[2])); "
            "print(info.name, info.compile_sdk, info.native_build)"
        )

        env = dict(os.environ)
        env.pop("PYTHONPATH", None)

        completed = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                probe,
                str(staged),
                str(project),
            ],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "standalone-fixture 36 False"


def test_refresh_repairs_stale_toolchain_companion() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "build-apk"

        staged = stage_android_builder(destination=target)
        companion = staged.with_name("termux_toolchains.py")

        companion.write_text(
            "stale companion\n",
            encoding="utf-8",
        )

        spec = CommandSpec(
            "android-validation",
            [
                "python3",
                str(staged),
                ".",
                "--toolchain-downloads",
                "never",
            ],
            metadata={
                "android_builder": str(staged),
            },
        )

        refresh_android_builders_for_specs([spec])

        expected = (
            android_build.standalone_companion_path()
            .read_bytes()
        )

        assert companion.read_bytes() == expected
