from __future__ import annotations

import tempfile
from pathlib import Path

from devtool.python_support import audit_source_tree, inspect_python_project


def _project(root: Path, *, dependencies: str = "[]", audit: str = "") -> None:
    (root / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='1.0.0'\n"
        f"dependencies={dependencies}\n"
        + audit,
        encoding="utf-8",
    )
    package = root / "demo"
    package.mkdir()
    (package / "__init__.py").write_text("__version__='1.0.0'\n", encoding="utf-8")


def test_default_scope_enforces_project_but_not_heterogeneous_dotfiles() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _project(root)
        (root / "demo" / "__init__.py").write_text("__version__='1.0.0'\nimport httpx\n", encoding="utf-8")
        tool = root / "dotfiles" / "termux" / "bin" / "backup"
        tool.parent.mkdir(parents=True)
        tool.write_text(
            "#!/usr/bin/env python3\nimport tqdm\nimport ctypes\n"
            "LIBC=ctypes.CDLL('libc.so.6', use_errno=True)\n",
            encoding="utf-8",
        )
        info = inspect_python_project(root, "venv", "pytest")
        payload, rc = audit_source_tree(root, info)

    assert rc == 1
    assert payload["dependency_audit_scope"] == "project"
    assert [(item["path"], item["code"]) for item in payload["issues"]] == [
        ("demo/__init__.py", "PYTHON_UNDECLARED_IMPORT"),
    ]
    assert payload["auxiliary_undeclared_imports"] == [{
        "code": "PYTHON_UNDECLARED_IMPORT",
        "path": "dotfiles/termux/bin/backup",
        "message": "Imports third-party module 'tqdm', but no matching distribution is declared in project/build/optional requirements.",
    }]
    assert payload["auxiliary_portability_findings"][0]["path"] == "dotfiles/termux/bin/backup"


def test_repository_scope_can_be_enabled_in_pyproject() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _project(root, audit="[tool.devtool.python-audit]\ndependency-scope='repository'\n")
        tool = root / "dotfiles" / "termux" / "bin" / "backup"
        tool.parent.mkdir(parents=True)
        tool.write_text(
            "#!/usr/bin/env python3\nimport tqdm\nimport ctypes\n"
            "LIBC=ctypes.CDLL('libc.so.6', use_errno=True)\n",
            encoding="utf-8",
        )
        info = inspect_python_project(root, "venv", "pytest")
        payload, rc = audit_source_tree(root, info)

    assert rc == 1
    assert payload["dependency_audit_scope"] == "repository"
    assert {item["code"] for item in payload["issues"]} == {
        "PYTHON_UNDECLARED_IMPORT",
        "PYTHON_PLATFORM_GLIBC",
    }


def test_command_override_can_relax_configured_repository_scope() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _project(root, audit="[tool.devtool.python-audit]\ndependency-scope='repository'\n")
        tool = root / "dotfiles" / "common" / "bin" / "tool"
        tool.parent.mkdir(parents=True)
        tool.write_text("#!/usr/bin/env python3\nimport tqdm\n", encoding="utf-8")
        info = inspect_python_project(root, "venv", "pytest")
        payload, rc = audit_source_tree(root, info, dependency_scope="project")

    assert rc == 0
    assert payload["dependency_audit_scope"] == "project"
    assert payload["issues"] == []
    assert payload["auxiliary_undeclared_imports"][0]["path"] == "dotfiles/common/bin/tool"


def test_captured_pip_console_launcher_is_not_dependency_enforced() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _project(root, audit="[tool.devtool.python-audit]\ndependency-scope='repository'\n")
        launcher = root / "dotfiles" / "host" / "bin" / "flask"
        launcher.parent.mkdir(parents=True)
        launcher.write_text(
            "#!/usr/bin/env python3\n"
            "import re\nimport sys\nfrom flask.cli import main\n"
            "if __name__ == '__main__':\n"
            "    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
            "    sys.exit(main())\n",
            encoding="utf-8",
        )
        info = inspect_python_project(root, "venv", "pytest")
        payload, rc = audit_source_tree(root, info)

    assert rc == 0
    assert payload["generated_console_launchers"] == ["dotfiles/host/bin/flask"]
    assert payload["issues"] == []


def test_dotfiles_import_inventory_does_not_create_36_package_errors() -> None:
    imports = [
        "inotify_simple", "PIL", "aiofiles", "aiohttp", "bs4", "playwright",
        "cryptography", "gi", "moviepy", "dotenv", "numpy", "flask",
        "charset_normalizer", "rsa", "tabulate", "filetype",
    ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _project(root)
        for index, module in enumerate(imports):
            tool = root / "dotfiles" / "profile" / "bin" / f"tool{index}"
            tool.parent.mkdir(parents=True, exist_ok=True)
            tool.write_text(f"#!/usr/bin/env python3\nimport {module}\n", encoding="utf-8")
        info = inspect_python_project(root, "venv", "pytest")
        payload, rc = audit_source_tree(root, info)

    assert rc == 0
    assert payload["errors"] == 0
    assert len(payload["auxiliary_undeclared_imports"]) == len(imports)
