from __future__ import annotations

import tempfile
from pathlib import Path

from devtool.python_support import audit_source_tree, inspect_python_project


def _write_project(root: Path, *, strict: bool = False) -> None:
    audit = (
        "\n[tool.devtool.python-audit]\ndependency-scope='repository'\n"
        if strict
        else ""
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        "name='dotcli'\n"
        "version='1.0.0'\n"
        "dependencies=[]\n"
        "[project.optional-dependencies]\n"
        "managed-scripts=['PyGObject>=3.50']\n"
        "[tool.setuptools.packages.find]\n"
        "include=['dotcli*']\n"
        + audit,
        encoding="utf-8",
    )
    package = root / "dotcli"
    package.mkdir()
    (package / "__init__.py").write_text("__version__='1.0.0'\n", encoding="utf-8")
    wallblur = root / "dotfiles" / "common" / "bin" / "wallblur"
    wallblur.parent.mkdir(parents=True)
    wallblur.write_text("#!/usr/bin/env python3\nimport gi\n", encoding="utf-8")
    # A .py file elsewhere in dotfiles used to make the entire top-level
    # dotfiles/ directory look like an import package when source_root == '.'.
    (wallblur.parent / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")


def test_setuptools_find_include_prevents_dotfiles_from_becoming_import_package() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_project(root)
        info = inspect_python_project(root, "venv", "pytest")

    assert info.import_packages == ("dotcli",)


def test_pygobject_distribution_satisfies_gi_import_in_strict_repository_scope() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_project(root, strict=True)
        info = inspect_python_project(root, "venv", "pytest")
        payload, rc = audit_source_tree(root, info)

    assert rc == 0
    assert payload["issues"] == []
    assert "gi" in payload["third_party_imports"]
    assert payload["dependency_audit_scope"] == "repository"


def test_custom_optional_dependency_group_is_included_in_declared_distributions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_project(root, strict=True)
        # Put gi in project-owned code too: this proves the custom extra name
        # managed-scripts participates in declaration matching, independently
        # of auxiliary-scope behavior.
        (root / "dotcli" / "feature.py").write_text("import gi\n", encoding="utf-8")
        info = inspect_python_project(root, "venv", "pytest")
        payload, rc = audit_source_tree(root, info)

    assert rc == 0
    assert payload["issues"] == []
    assert "gi" in payload["third_party_imports"]
