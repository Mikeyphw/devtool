from __future__ import annotations

import tempfile
import sys
from pathlib import Path

from devtool.diagnostics import parse_issues
from devtool.gradle_reports import collect_pytest_report
from devtool.models import CommandSpec, ProjectConfig
from devtool.python_support import audit_source_tree, inspect_python_project
from devtool.runners import run_specs
from devtool.summary import _root_cause_issue, build_json_summary


def _write_pytest_report(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cases = [
        ("tests.test_schema", "test_yaml_runtime", "RuntimeError: PyYAML is required for config support. Install it with: pip install pyyaml"),
        ("tests.test_schema", "test_yaml_import", "ModuleNotFoundError: No module named 'yaml'"),
        ("tests.test_schema", "test_libc_one", 'OSError: dlopen failed: library "libc.so.6" not found'),
        ("tests.test_schema", "test_libc_two", 'OSError: dlopen failed: library "libc.so.6" not found'),
        ("tests.test_backup", "test_tqdm", "ModuleNotFoundError: No module named 'tqdm'"),
        ("tests.test_zsh", "test_doctor", "assert False"),
    ]
    xml = [f'<testsuite tests="6" failures="6" skipped="0">']
    for index, (classname, name, message) in enumerate(cases, start=1):
        file_name = classname.replace(".", "/") + ".py"
        escaped = (
            message.replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        xml.append(
            f'<testcase classname="{classname}" name="{name}" file="{file_name}" line="{index}">'
            f'<failure message="{escaped}"/></testcase>'
        )
    xml.append("</testsuite>")
    path.write_text("".join(xml), encoding="utf-8")


def test_pytest_junit_uses_python_codes_and_categories() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        report = root / "pytest.xml"
        _write_pytest_report(report)
        collection = collect_pytest_report(root, report_path=str(report))

    assert len(collection.issues) == 6
    codes = [issue.code for issue in collection.issues]
    assert "GRADLE_TEST" not in codes
    assert codes.count("PYTHON_DEPENDENCY") == 3
    assert codes.count("PYTHON_PLATFORM") == 2
    assert codes.count("PYTEST_FAILURE") == 1
    assert collection.metadata["failure_categories"] == {
        "dependency": 3,
        "platform": 2,
        "test": 1,
    }


def test_pytest_warning_summary_becomes_structured_warning() -> None:
    root = Path("/repo")
    issues = parse_issues(
        root,
        "test",
        "/repo/.venv/lib/python3.14/site-packages/fastapi/testclient.py:1: "
        "StarletteDeprecationWarning: Using httpx is deprecated\n"
        "/repo/tests/test_web.py:45: DeprecationWarning: cookies= is deprecated\n",
    )
    warnings = [issue for issue in issues if issue.severity == "warning"]
    assert [(item.code, item.file, item.line) for item in warnings] == [
        ("PYTEST_WARNING", ".venv/lib/python3.14/site-packages/fastapi/testclient.py", 1),
        ("PYTEST_WARNING", "tests/test_web.py", 45),
    ]


def test_run_specs_deduplicates_console_failures_against_pytest_junit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        report = root / ".devtool" / "reports" / "pytest.xml"
        script = root / "emit.py"
        script.write_text(
            "from pathlib import Path\n"
            f"p=Path({str(report)!r}); p.parent.mkdir(parents=True, exist_ok=True)\n"
            "cases=[\n"
            "('tests.test_schema','test_yaml_runtime','RuntimeError: PyYAML is required for config support. Install it with: pip install pyyaml'),\n"
            "('tests.test_schema','test_yaml_import',\"ModuleNotFoundError: No module named 'yaml'\"),\n"
            "('tests.test_schema','test_libc_one','OSError: dlopen failed: library \\\"libc.so.6\\\" not found'),\n"
            "('tests.test_schema','test_libc_two','OSError: dlopen failed: library \\\"libc.so.6\\\" not found'),\n"
            "('tests.test_backup','test_tqdm',\"ModuleNotFoundError: No module named 'tqdm'\"),\n"
            "('tests.test_zsh','test_doctor','assert False')]\n"
            "import xml.etree.ElementTree as ET\n"
            "suite=ET.Element('testsuite', tests='6', failures='6', skipped='0')\n"
            "for i,(cls,name,msg) in enumerate(cases,1):\n"
            " c=ET.SubElement(suite,'testcase',classname=cls,name=name,file=cls.replace('.','/')+'.py',line=str(i)); ET.SubElement(c,'failure',message=msg)\n"
            "ET.ElementTree(suite).write(p, encoding='unicode')\n"
            "for cls,name,msg in cases: print(f'FAILED {cls.replace(\".\",\"/\")}.py::{name} - {msg}')\n"
            "print('/tmp/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: httpx is deprecated')\n"
            "print('/tmp/tests/test_web.py:45: DeprecationWarning: cookies= is deprecated')\n"
            "raise SystemExit(1)\n",
            encoding="utf-8",
        )
        config = ProjectConfig(repo_root=root, project_root=str(root), kind="python")
        spec = CommandSpec(
            "test",
            [sys.executable, "-S", str(script)],
            cwd=str(root),
            metadata={
                "project_root": str(root),
                "report_parser": "pytest-junit",
                "test_report": str(report),
            },
        )
        results, _ = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)
        result = results[0]
        summary = build_json_summary(results, repo_root=str(root))

    errors = [issue for issue in result.issues if issue.severity == "error"]
    warnings = [issue for issue in result.issues if issue.severity == "warning"]
    assert len(errors) == 6
    assert len(warnings) == 2
    assert all(issue.code not in {"GRADLE_TEST", "PYTEST"} for issue in errors)
    assert result.failure_kind == "test_failure"
    assert result.metadata["tests"]["failure_categories"] == {
        "dependency": 3,
        "platform": 2,
        "test": 1,
    }
    assert summary["errors"] == 6
    assert summary["warnings"] == 2
    assert _root_cause_issue(result).code == "PYTHON_DEPENDENCY"


def test_source_audit_scans_extensionless_tools_and_termux_portability() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pyproject.toml").write_text(
            "[build-system]\nrequires=['setuptools>=69']\nbuild-backend='setuptools.build_meta'\n"
            "[project]\nname='demo'\nversion='1.0.0'\ndependencies=['rich','ruamel-yaml']\n",
            encoding="utf-8",
        )
        package = root / "demo"
        package.mkdir()
        (package / "__init__.py").write_text("__version__='1.0.0'\n", encoding="utf-8")
        bindir = root / "dotfiles" / "termux" / "bin"
        bindir.mkdir(parents=True)
        tool = bindir / "backup"
        tool.write_text(
            "#!/usr/bin/env python3\n"
            "import tqdm\n"
            "import ctypes\n"
            "LIBC = ctypes.CDLL('libc.so.6', use_errno=True)\n",
            encoding="utf-8",
        )
        zrun = root / "dotfiles" / "common" / "bin" / "zrun"
        zrun.parent.mkdir(parents=True)
        zrun.write_text(
            "#!/usr/bin/env python3\n"
            "try:\n import yaml\nexcept ModuleNotFoundError:\n yaml=None\n"
            "if yaml is None: raise RuntimeError('PyYAML is required; install it with pip install pyyaml')\n",
            encoding="utf-8",
        )
        info = inspect_python_project(root, "venv", "pytest")
        payload, rc = audit_source_tree(root, info)

    # Default project scope still scans auxiliary scripts but keeps their
    # host-specific dependencies outside the installable package contract.
    assert rc == 0
    assert payload["dependency_audit_scope"] == "project"
    assert {item["path"] for item in payload["auxiliary_undeclared_imports"]} == {
        "dotfiles/common/bin/zrun",
        "dotfiles/termux/bin/backup",
    }
    assert payload["auxiliary_portability_findings"][0]["path"] == "dotfiles/termux/bin/backup"
    assert "dotfiles/common/bin/zrun" in payload["audited_python_files"]
    assert "dotfiles/termux/bin/backup" in payload["audited_python_files"]


def test_source_audit_allows_explicit_system_and_optional_fallback_imports() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pyproject.toml").write_text(
            "[project]\nname='demo'\nversion='1.0.0'\ndependencies=[]\n",
            encoding="utf-8",
        )
        package = root / "demo"
        package.mkdir()
        (package / "__init__.py").write_text(
            "try:\n from packaging.version import Version\nexcept ImportError:\n Version=None\n"
            "def load_gtk():\n import gi  # type: ignore[import-not-found]\n return gi\n",
            encoding="utf-8",
        )
        info = inspect_python_project(root, "venv", "pytest")
        payload, rc = audit_source_tree(root, info)

    assert rc == 0
    assert payload["issues"] == []


def test_failure_clipboard_uses_canonical_python_diagnostics(monkeypatch) -> None:
    import argparse
    import json
    from types import SimpleNamespace

    import devtool.cli as cli

    issues = [
        # Same six canonical identities as the dotcli failure; raw output below
        # deliberately repeats them via pytest's FAILED lines.
        *[
            __import__("devtool.models", fromlist=["Issue"]).Issue("error", "test", f"tests/t{i}.py", code, message)
            for i, (code, message) in enumerate(
                [
                    ("PYTHON_DEPENDENCY", "a: ModuleNotFoundError: No module named 'yaml'"),
                    ("PYTHON_DEPENDENCY", "b: PyYAML is required"),
                    ("PYTHON_DEPENDENCY", "c: ModuleNotFoundError: No module named 'tqdm'"),
                    ("PYTHON_PLATFORM", 'd: OSError: dlopen failed: library "libc.so.6" not found'),
                    ("PYTHON_PLATFORM", 'e: OSError: dlopen failed: library "libc.so.6" not found'),
                    ("PYTEST_FAILURE", "f: assert False"),
                ],
                start=1,
            )
        ],
        __import__("devtool.models", fromlist=["Issue"]).Issue("warning", "test", "fastapi/testclient.py", "PYTEST_WARNING", "StarletteDeprecationWarning: httpx deprecated", line=1),
        __import__("devtool.models", fromlist=["Issue"]).Issue("warning", "test", "tests/test_web.py", "PYTEST_WARNING", "DeprecationWarning: cookies deprecated", line=45),
    ]
    result = __import__("devtool.models", fromlist=["OperationResult"]).OperationResult(
        "test", ["pytest"], 1, 1.0,
        "\n".join([
            "FAILED tests/t1.py::a - ModuleNotFoundError: No module named 'yaml'",
            "FAILED tests/t2.py::b - PyYAML is required",
            "FAILED tests/t3.py::c - ModuleNotFoundError: No module named 'tqdm'",
            'FAILED tests/t4.py::d - OSError: dlopen failed: library "libc.so.6" not found',
            'FAILED tests/t5.py::e - OSError: dlopen failed: library "libc.so.6" not found',
            "FAILED tests/t6.py::f - assert False",
            "fastapi/testclient.py:1: StarletteDeprecationWarning: httpx deprecated",
            "tests/test_web.py:45: DeprecationWarning: cookies deprecated",
        ]),
        issues,
        failure_kind="test_failure",
        metadata={"tests": {"passed": 633, "failed": 6, "skipped": 1, "total": 640, "failure_categories": {"dependency": 3, "platform": 2, "test": 1}}},
    )
    payload = build_json_summary([result], repo_root="/repo")
    captured = {}

    def fake_copy(text, preferred=None):
        captured["text"] = text
        return SimpleNamespace(success=True, backend="test", reason="")

    monkeypatch.setattr(cli, "copy_text", fake_copy)
    args = argparse.Namespace(
        no_clipboard=False,
        copy=True,
        copy_via="auto",
        command="apply-overlay",
    )
    cli._maybe_copy(result.output, "test: failed exit=1 time=1.0s", json.dumps(payload), args)
    text = captured["text"]
    errors_block = text.split("Errors:\n", 1)[1].split("\n\nWarnings:", 1)[0]
    warnings_block = text.split("Warnings:\n", 1)[1].split("\n\n", 1)[0]
    assert sum(1 for line in errors_block.splitlines() if line.startswith("- ")) == 6
    assert sum(1 for line in warnings_block.splitlines() if line.startswith("- ")) == 2
    assert "GRADLE_TEST" not in text
    assert "test failed with exit 1: a: ModuleNotFoundError" in text
