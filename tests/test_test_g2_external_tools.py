from __future__ import annotations

import subprocess

import pytest


def _run(*argv: str) -> str:
    result = subprocess.run(list(argv), text=True, capture_output=True, check=False, timeout=30)
    text = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    assert result.returncode == 0, text
    return text


@pytest.mark.external
@pytest.mark.requires_tool("gradle")
@pytest.mark.public_contract
@pytest.mark.devtool_check("test.external.gradle-smoke")
@pytest.mark.behavior("test-system.external.gradle")
def test_real_gradle_tool_when_available() -> None:
    text = _run("gradle", "--version")
    assert "Gradle" in text


@pytest.mark.external
@pytest.mark.requires_tool("go")
@pytest.mark.public_contract
@pytest.mark.devtool_check("test.external.go-smoke")
@pytest.mark.behavior("test-system.external.go")
def test_real_go_tool_when_available() -> None:
    text = _run("go", "version")
    assert text.lower().startswith("go version")


@pytest.mark.external
@pytest.mark.requires_tool("javac")
@pytest.mark.requires_tool("java")
@pytest.mark.public_contract
@pytest.mark.devtool_check("test.external.javac-smoke")
@pytest.mark.behavior("test-system.external.javac")
def test_real_javac_and_java_tools_when_available() -> None:
    javac = _run("javac", "-version")
    java = _run("java", "-version")
    assert "javac" in javac.lower()
    assert "version" in java.lower()
