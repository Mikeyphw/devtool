"""Negative evidence path and provenance checks for R04B historical audits."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from devtool.remed_r04b_fixed_point import _resolve_reference


def test_r04b_rejects_absolute_parent_traversal_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("untrusted evidence", encoding="utf-8")
    (root / "linked.py").symlink_to(outside)
    for ref in ("../outside.py", "file:../outside.py", "source:../outside.py", str(outside), "source:" + str(outside), "linked.py", "source:linked.py", "linked.py::test_fake"):
        result = _resolve_reference(root, ref, source_rows={})
        assert result["resolved"] is False, (ref, result)
        assert result["sha256"] == "", (ref, result)


def test_r04b_keeps_valid_local_files_and_source_rows(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    file = root / "valid.py"
    file.write_text("original source", encoding="utf-8")
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    for ref in ("valid.py", "file:valid.py", "source:valid.py", "valid.py::test_foo"):
        result = _resolve_reference(root, ref, source_rows={})
        assert result["resolved"] is True, (ref, result)
        assert result["sha256"] == digest
    row_hash = "a" * 64
    assert _resolve_reference(root, f"source:TEST-P001:{row_hash}", source_rows={"TEST-P001":{"source_row_sha256":row_hash}})["resolved"]
    assert not _resolve_reference(root, f"source:TEST-P001:{'0'*64}", source_rows={"TEST-P001":{"source_row_sha256":row_hash}})["resolved"]
