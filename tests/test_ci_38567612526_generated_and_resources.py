"""Repair 02: parser-derived documentation and explicit resource contract.

This file checks the actual producer output rather than replacing expected
historical G3/G8 evidence hashes or bypassing release qualification.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from devtool.build_optimization import resolve_build_policy
from devtool.cli import build_parser
from devtool.completion import ZSH_COMPLETION
from devtool.core_surfaces import build_generated_surfaces, check_generated_surfaces
from devtool.models import ProjectConfig

ROOT = Path(__file__).resolve().parents[1]


def test_core_generated_surfaces_are_current_and_hash_bound() -> None:
    bundle = build_generated_surfaces(build_parser())
    check = check_generated_surfaces(bundle, ROOT / "share/core-generated")
    assert check["status"] == "pass", check


def test_legacy_zsh_completion_is_exact_runtime_projection() -> None:
    assert (ROOT / "completions/_devtool").read_text(encoding="utf-8") == ZSH_COMPLETION


def test_explicit_workers_take_precedence_over_host_cpu_guess() -> None:
    cfg = ProjectConfig(repo_root=ROOT, kind="go")
    with patch("devtool.build_optimization.os.cpu_count", return_value=2), patch(
        "devtool.build_optimization.available_memory_mb", return_value=512
    ), patch("devtool.build_optimization._explicit_workers", return_value=3):
        policy = resolve_build_policy(cfg, "go")
    assert policy.cpu_count == 2
    assert policy.max_workers == 3


def test_implicit_workers_still_adhere_to_cpu_limit() -> None:
    cfg = ProjectConfig(repo_root=ROOT, kind="go")
    with patch("devtool.build_optimization.os.cpu_count", return_value=2), patch(
        "devtool.build_optimization.available_memory_mb", return_value=16000
    ), patch("devtool.build_optimization._explicit_workers", return_value=None):
        policy = resolve_build_policy(cfg, "go")
    assert 1 <= policy.max_workers <= 2


def test_abnormally_large_explicit_workers_are_bounded() -> None:
    cfg = ProjectConfig(repo_root=ROOT, kind="go")
    with patch("devtool.build_optimization.os.cpu_count", return_value=2), patch(
        "devtool.build_optimization.available_memory_mb", return_value=16000
    ), patch("devtool.build_optimization._explicit_workers", return_value=100000):
        policy = resolve_build_policy(cfg, "go")
    assert policy.max_workers == 64
