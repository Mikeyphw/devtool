from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from devtool.cli import build_parser
from devtool.desktop import control_center_cli, gtk_hud_cli
from devtool.desktop.gtk_runtime import append_gtk_log, redact_text, wrap_periodic_callback

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_gtk_runtime_redacts_common_secret_forms(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    raw = "token=ghp_1234567890abcdefghijklmnop Authorization: Bearer sk-secretsecretsecret password=hunter2"
    redacted = redact_text(raw)
    assert "ghp_1234567890" not in redacted
    assert "sk-secretsecretsecret" not in redacted
    assert "hunter2" not in redacted
    path = append_gtk_log("control-center", raw, RuntimeError(raw))
    text = path.read_text(encoding="utf-8")
    assert "<redacted" in text
    assert "hunter2" not in text


def test_direct_gtk_launchers_log_unexpected_exceptions_without_traceback_leak(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with patch("devtool.desktop.control_center_cli.Gtk4ControlCenter.available", return_value=True), \
         patch("devtool.desktop.control_center_cli.Gtk4ControlCenter.run", side_effect=ValueError("token=supersecret")):
        assert control_center_cli.main([]) == 2
    err = capsys.readouterr().err
    assert "devtool-control-center:" in err
    assert "supersecret" not in err
    assert (tmp_path / "devtool" / "control-center.log").is_file()

    with patch("devtool.desktop.gtk_hud_cli.Gtk4CompactHud.run", side_effect=TypeError("password=supersecret")):
        assert gtk_hud_cli.main([]) == 2
    err = capsys.readouterr().err
    assert "devtool-gtk-hud:" in err
    assert "supersecret" not in err
    assert (tmp_path / "devtool" / "hud.log").is_file()


def test_periodic_gtk_callback_logs_and_keeps_timer_alive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    seen: list[str] = []

    def broken() -> bool:
        raise RuntimeError("api_key=abc123")

    wrapped = wrap_periodic_callback(app_slug="hud", callback=broken, on_error=lambda _exc, path: seen.append(path.name))
    assert wrapped() is True
    assert seen == ["hud.log"]
    text = (tmp_path / "devtool" / "hud.log").read_text(encoding="utf-8")
    assert "abc123" not in text
    assert "GTK periodic callback failed" in text


def test_control_center_page_choices_include_artifacts_and_startup_test() -> None:
    parser = build_parser()
    args = parser.parse_args(["control-center", "--page", "artifacts", "--startup-test"])
    assert args.page == "artifacts"
    assert args.startup_test is True

    direct = control_center_cli.build_parser()
    direct_args = direct.parse_args(["--page", "artifacts", "--startup-test"])
    assert direct_args.page == "artifacts"
    assert direct_args.startup_test is True
    assert gtk_hud_cli.build_parser().parse_args(["--startup-test"]).startup_test is True


def test_gtk_activation_boundaries_catch_all_exceptions_and_show_error_windows() -> None:
    control_source = read(ROOT / "devtool" / "desktop" / "gtk_control_center.py")
    hud_source = read(ROOT / "devtool" / "desktop" / "gtk_hud.py")
    assert "except Exception as exc" in control_source
    assert "show_startup_error_window" in control_source
    assert "wrap_periodic_callback" in control_source
    assert "except Exception as exc" in hud_source
    assert "show_startup_error_window" in hud_source
    assert "wrap_periodic_callback" in hud_source


def test_debian_launchers_use_distro_python_and_installer_refuses_unimplemented_clipboard_monitor() -> None:
    package_script = read(ROOT / "desktop" / "gtk-control-center" / "package-deb.sh")
    install_script = read(ROOT / "install.sh")
    assert "#!/usr/bin/python3" in package_script
    assert "#!/usr/bin/env python3" not in package_script
    assert "must use /usr/bin/python3" in package_script
    assert "unsupported --clipboard-monitor" in install_script
    assert 'ExecStart="$clipboard_binary" --clipboard-monitor' not in install_script
