"""Native Android CPython and Termux detection regression contract."""
from __future__ import annotations

import pytest
from devtool import g3r_evidence_generate as generator


@pytest.mark.parametrize("system", ["Linux", "Android"])
def test_termux_prefix_is_recognized_on_supported_native_python_platforms(monkeypatch, system):
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    monkeypatch.setattr(generator.platform, "system", lambda: system)
    assert generator.native_termux_environment() is True


@pytest.mark.parametrize("system,prefix", [
    ("Linux", "/usr"), ("Android", ""),
    ("Windows", "/data/data/com.termux/files/usr"),
])
def test_non_termux_or_unsupported_platforms_are_not_native(monkeypatch, system, prefix):
    monkeypatch.setenv("PREFIX", prefix)
    monkeypatch.setattr(generator.platform, "system", lambda: system)
    assert generator.native_termux_environment() is False
