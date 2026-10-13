from __future__ import annotations

import io
from pathlib import Path
import subprocess

from rich.console import Console

from devtool.cli import build_parser
from devtool.repository_cleanup import cleanup_repository, discover_backup_candidates


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Devtool Test"], cwd=root, check=True)
    (root / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)






def test_textual_dashboard_respects_narrow_terminal_policy() -> None:
    from devtool.textual_control_center import TextualControlCenter, render_control_center_text
    screen = TextualControlCenter(page="dashboard").snapshot(width=40, height=20)
    assert screen["page"] == "dashboard"
    assert screen["owns_truth"] is False
    assert "dashboard" in render_control_center_text(screen).lower()


def test_textual_overlay_page_uses_shared_responsive_contract() -> None:
    from devtool.textual_control_center import TextualControlCenter
    from devtool.ui_core.responsive import resolve_responsive_layout
    narrow = TextualControlCenter(page="overlays").snapshot(width=58, height=20)
    wide = TextualControlCenter(page="overlays").snapshot(width=120, height=30)
    assert narrow["page"] == wide["page"] == "overlays"
    assert resolve_responsive_layout("tui", width=58).mode == "single-pane"
    assert resolve_responsive_layout("tui", width=120).mode == "three-pane"


def test_cleanup_archives_backups_but_preserves_regular_files(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    backup = tmp_path / "devtool" / "cli.py.before-termux-fix"
    backup.parent.mkdir()
    backup.write_text("old\n", encoding="utf-8")
    backup_dir = tmp_path / ".warning-fix-backup-20260825"
    backup_dir.mkdir()
    (backup_dir / "state.txt").write_text("old\n", encoding="utf-8")
    regular = tmp_path / "devtool" / "cli.py"
    regular.write_text("current\n", encoding="utf-8")

    candidates = {path.relative_to(tmp_path).as_posix() for path in discover_backup_candidates(tmp_path)}
    assert "devtool/cli.py.before-termux-fix" not in candidates
    assert ".warning-fix-backup-20260825" in candidates
    assert "devtool/cli.py" not in candidates

    payload = cleanup_repository(tmp_path)
    assert payload["archived_count"] == 1
    assert backup.exists(), "unknown backup-shaped data must be preserved unless ownership is proven"
    assert not backup_dir.exists()
    assert regular.read_text(encoding="utf-8") == "current\n"
    archived = list((tmp_path / ".devtool" / "history" / "backups").rglob("cli.py.before-termux-fix"))
    assert archived == []


def test_cleanup_dry_run_does_not_move_files(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    backup = tmp_path / ".warning-fix-backup-dry-run"
    backup.mkdir()
    (backup / "state.txt").write_text("old\n", encoding="utf-8")
    payload = cleanup_repository(tmp_path, dry_run=True)
    assert payload["archived_count"] == 1
    assert backup.is_dir()


def test_cli_exposes_tui_and_cleanup_commands() -> None:
    parser = build_parser()
    tui = parser.parse_args(["tui", "--page", "overlays", "--once", "--width", "48"])
    assert tui.command == "tui"
    assert tui.page == "overlays"
    assert tui.width == 48
    cleanup = parser.parse_args(["cleanup", "--dry-run", "--prune-history-days", "30"])
    assert cleanup.command == "cleanup"
    assert cleanup.dry_run is True
    assert cleanup.prune_history_days == 30.0
