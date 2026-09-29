from __future__ import annotations

import io
from pathlib import Path
import subprocess

from rich.console import Console

from devtool.cli import build_parser
from devtool.repository_cleanup import cleanup_repository, discover_backup_candidates
from devtool.tui import render_page, terminal_profile


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Devtool Test"], cwd=root, check=True)
    (root / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)


def _render_text(root: Path, *, page: str, width: int) -> str:
    renderable, _ = render_page(root, page=page, width=width, height=24)
    stream = io.StringIO()
    console = Console(file=stream, width=width, color_system=None, force_terminal=False)
    console.print(renderable)
    return stream.getvalue()


def test_terminal_profiles_cover_termux_narrow_breakpoints() -> None:
    assert terminal_profile(width=40, height=20).mode == "compact"
    assert terminal_profile(width=60, height=20).mode == "compact"
    assert terminal_profile(width=61, height=20).mode == "narrow"
    assert terminal_profile(width=96, height=20).mode == "narrow"
    assert terminal_profile(width=97, height=20).mode == "wide"


def test_dashboard_stays_within_40_column_terminal(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    text = _render_text(tmp_path, page="dashboard", width=40)
    assert "Repository" in text
    assert "Overview" in text
    assert all(len(line) <= 40 for line in text.splitlines())


def test_build_and_overlay_pages_drop_wide_columns_on_narrow_terminals(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    registry = tmp_path / ".devtool" / "artifacts" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        '{"schema_version":1,"items":[{"name":"very-long-artifact-name-debug.apk","path":".devtool/build-artifacts/x.apk","size":123,"valid":true,"commit":"0123456789abcdef","registered_at":"2026-08-25T00:00:00Z"}]}',
        encoding="utf-8",
    )
    narrow = _render_text(tmp_path, page="build", width=58)
    wide = _render_text(tmp_path, page="build", width=120)
    assert "Registered" not in narrow
    assert "Registered" in wide
    assert all(len(line) <= 58 for line in narrow.splitlines())


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
