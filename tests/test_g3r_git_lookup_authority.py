"""R04B must not manufacture empty authority identities on Git errors."""
from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

import pytest
from devtool import remed_r04b_fixed_point as r04b


def test_git_lookup_uses_local_safe_directory_without_global_changes(tmp_path, monkeypatch):
    seen = []
    def fake_run(args, **kwargs):
        seen.append((args, kwargs))
        return CompletedProcess(args, 0, "0123456789abcdef\n", "")
    monkeypatch.setattr(r04b.subprocess, "run", fake_run)
    assert r04b._git_value(tmp_path, "rev-parse", "HEAD") == "0123456789abcdef"
    assert seen[0][0][:3] == ["git", "-c", f"safe.directory={tmp_path.resolve()}"]
    assert seen[0][1]["cwd"] == tmp_path.resolve()


@pytest.mark.parametrize("code,stdout,stderr", [(128, "", "dubious ownership"), (0, "", "")])
def test_git_lookup_fails_closed(tmp_path, monkeypatch, code, stdout, stderr):
    monkeypatch.setattr(r04b.subprocess, "run", lambda args, **kwargs: CompletedProcess(args, code, stdout, stderr))
    with pytest.raises(RuntimeError, match="R04B Git authority lookup failed"):
        r04b._git_value(tmp_path, "rev-parse", "HEAD")
