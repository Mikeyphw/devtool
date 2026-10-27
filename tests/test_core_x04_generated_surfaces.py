from __future__ import annotations

import json
import os
import time
from pathlib import Path
import subprocess
import sys

import pytest

from devtool.cli import build_parser
from devtool.core_registry import build_adopted_core_registry
from devtool.core_surfaces import (
    GENERATED_SURFACE_FILES,
    PERFORMANCE_BUDGETS_MS,
    build_generated_surfaces,
    check_generated_surfaces,
    filter_machine_help,
    generated_completion,
    write_generated_surfaces,
)
from devtool.run_store import RunCatalog, RunQuery, migrate_legacy_run_storage, run_storage_status
from devtool.wrapper_launcher import (
    POSIX_LAUNCHER,
    WINDOWS_LAUNCHER,
    install_launchers,
)
from devtool.wrapper_migration import migrate_wrapper_schema_text


def test_complete_parser_surface_is_adopted_without_bumping_registry_wire_schema() -> None:
    parser = build_parser()
    registry = build_adopted_core_registry(parser)
    parity = registry.parser_parity(parser)

    assert registry.schema_version == 1
    assert parity["missing_count"] == 0
    assert parity["parser_command_count"] >= 250
    assert len(registry.commands) >= 200
    assert registry.resolve_command(["core", "surfaces"])["command"].stable_id == "cli.command.core.surfaces"


def test_generated_surfaces_cover_help_schema_completion_reference_and_manpage(tmp_path: Path) -> None:
    bundle = build_generated_surfaces(build_parser())
    assert bundle.machine_help["parser_parity"]["missing_count"] == 0
    assert bundle.machine_help["registry_digest"] == bundle.registry.digest
    assert bundle.config_schema["registry_digest"] == bundle.registry.digest
    assert set(bundle.completions) == {"zsh", "bash", "fish"}
    assert "apply-overlay" not in bundle.completions["zsh"]
    assert "apply" in bundle.completions["zsh"]
    assert "Devtool generated CORE reference" in bundle.reference
    assert ".TH DEVTOOL-CORE 1" in bundle.manpage

    manifest = write_generated_surfaces(bundle, tmp_path)
    assert manifest["bundle_digest"] == bundle.digest
    assert set(GENERATED_SURFACE_FILES).issubset({p.name for p in tmp_path.iterdir()})
    assert check_generated_surfaces(bundle, tmp_path)["status"] == "pass"

    (tmp_path / "anonymous-history.json").write_text("{}\n", encoding="utf-8")
    drift = check_generated_surfaces(bundle, tmp_path)
    assert drift["status"] == "fail"
    assert {item["reason"] for item in drift["drift"]} >= {"unclassified-generated-output"}


def test_generated_config_schema_uses_real_wrapper_schema_key() -> None:
    bundle = build_generated_surfaces(build_parser())
    repo_fields = bundle.config_schema["documents"]["repo"]["fields"]
    wrapper_schema = next(item for item in repo_fields if item["field_id"] == "cfg.wrapper.schema_version")
    assert wrapper_schema["path"] == ["wrapper", "schema"]


def test_checked_in_generated_surfaces_are_current() -> None:
    bundle = build_generated_surfaces(build_parser())
    result = check_generated_surfaces(bundle, Path("share/core-generated"))
    assert result["status"] == "pass", result


def test_run_catalog_read_only_open_and_query_do_not_create_storage(tmp_path: Path) -> None:
    root = tmp_path / "missing" / "run-v3"
    catalog = RunCatalog.open(root, create=False)
    assert not root.exists()
    assert catalog.query(RunQuery()) == []
    assert not root.exists()
    assert catalog.doctor()["run_count"] == 0
    assert not root.exists()


def test_wrapper_schema_migration_is_minimal_and_idempotent() -> None:
    source = """# keep me\n[wrapper]\n# schema comment\nschema = 1 # legacy\ndefault_command = \"test\"\n\n[wrapper.commands.test]\ndevtool = \"test\"\n"""
    first = migrate_wrapper_schema_text(source)
    assert first.changed is True
    assert "schema = 2 # legacy" in first.migrated_text
    assert "# keep me" in first.migrated_text
    assert "# schema comment" in first.migrated_text

    second = migrate_wrapper_schema_text(first.migrated_text)
    assert second.changed is False
    assert second.migrated_text == first.migrated_text
    assert second.source_sha256 == second.migrated_sha256


def _cli_subprocess_smoke_timeout() -> int:
    """Bound a *full CLI import* smoke test, not the CORE performance budget.

    Termux Python imports the complete source-bound Devtool parser and can have
    cold-start variance under device resource pressure. Keep the existing 20s
    smoke bound on ordinary hosts and a finite 60s bound on native Termux.
    The separately enforced generated-surface budgets remain unchanged.
    """
    executable = str(Path(sys.executable).resolve())
    return 60 if "/com.termux/files/" in executable else 20


def test_cli_smoke_timeout_is_host_bounded_not_generation_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "executable", "/data/data/com.termux/files/usr/bin/python3")
    assert _cli_subprocess_smoke_timeout() == 60
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    assert _cli_subprocess_smoke_timeout() == 20
    assert PERFORMANCE_BUDGETS_MS["generated_surfaces"] == 5000


def test_wrapper_launcher_argv_forwarding_without_python_import(tmp_path: Path) -> None:
    """Verify the launcher boundary independently from an expensive CLI import."""
    probe = tmp_path / "argv-probe"
    probe.write_text("#!/bin/sh\nprintf 'ARG:%s\\n' \"$@\"\n", encoding="utf-8")
    probe.chmod(0o700)
    env = os.environ.copy()
    env["DEVTOOL_WRAPPER_EXECUTABLE"] = str(probe)
    env["PATH"] = "/usr/bin:/bin"
    result = subprocess.run(
        [str(Path("devtoolw").resolve()), "--help", "--", "argument with spaces"],
        cwd=Path.cwd(), env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=5, check=False,
    )
    assert result.returncode == 0, result.stdout
    assert result.stdout.splitlines() == [
        "ARG:--repo", f"ARG:{Path.cwd().resolve()}",
        "ARG:wrapper", "ARG:exec", "ARG:--help", "ARG:--",
        "ARG:argument with spaces",
    ]


def test_devtool_repo_dogfoods_schema2_wrapper_and_source_aware_launchers() -> None:
    config = Path(".devtool.toml").read_text(encoding="utf-8")
    assert "[wrapper]\n" in config
    assert "schema = 2" in config
    assert "[wrapper.commands.test]" in config
    assert Path("devtoolw").read_bytes() == Path("devtool/resources/wrapper/devtoolw").read_bytes()
    assert Path("devtoolw.cmd").read_bytes() == Path("devtool/resources/wrapper/devtoolw.cmd").read_bytes()

    env = os.environ.copy()
    env["DEVTOOL_WRAPPER_PYTHON"] = sys.executable
    env["PATH"] = "/usr/bin:/bin"
    result = subprocess.run(
        [str(Path("devtoolw").resolve()), "--help"],
        cwd=Path.cwd(),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=_cli_subprocess_smoke_timeout(),
        check=False,
    )
    assert result.returncode == 0, result.stdout
    assert "Repository wrapper" in result.stdout or "Commands" in result.stdout


def test_launcher_pair_update_rolls_back_first_member_if_second_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    posix = root / POSIX_LAUNCHER
    windows = root / WINDOWS_LAUNCHER
    old_posix = "#!/bin/sh\n# DEVTOOL_WRAPPER_LAUNCHER=1\necho old-posix\n"
    old_windows = "@echo off\r\nrem DEVTOOL_WRAPPER_LAUNCHER=1\r\necho old-windows\r\n"
    posix.write_text(old_posix, encoding="utf-8")
    posix.chmod(0o755)
    windows.write_text(old_windows, encoding="utf-8", newline="")

    import devtool.wrapper_launcher as launcher

    real_replace = launcher.os.replace
    failed = False

    def flaky_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        nonlocal failed
        destination = Path(dst)
        if destination.name == WINDOWS_LAUNCHER and not failed:
            failed = True
            raise OSError("simulated second-member replacement failure")
        real_replace(src, dst)

    monkeypatch.setattr(launcher.os, "replace", flaky_replace)
    with pytest.raises(OSError, match="second-member"):
        install_launchers(root, update=True)

    assert posix.read_text(encoding="utf-8") == old_posix
    assert windows.read_text(encoding="utf-8") == old_windows.replace("\r\n", "\n")
    assert not list(root.glob(".devtoolw.*.tmp"))
    assert not list(root.glob(".devtoolw.cmd.*.tmp"))


def test_generated_manpage_contains_real_line_boundaries() -> None:
    bundle = build_generated_surfaces(build_parser())
    assert "\n.SH NAME\n" in bundle.manpage
    assert "\\n.SH NAME\\n" not in bundle.manpage


def test_core_generated_surface_build_stays_inside_declared_budget() -> None:
    started = time.perf_counter()
    bundle = build_generated_surfaces(build_parser())
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert bundle.machine_help["parser_parity"]["missing_count"] == 0
    assert elapsed_ms < PERFORMANCE_BUDGETS_MS["generated_surfaces"]
    assert bundle.to_dict()["performance_budgets_ms"] == PERFORMANCE_BUDGETS_MS


def test_config_status_and_diff_are_machine_readable_and_side_effect_free() -> None:
    before = Path(".devtool.toml").read_bytes()
    for action in ("status", "diff"):
        result = subprocess.run(
            [sys.executable, "-m", "devtool", "--repo", str(Path.cwd()), "config", action, "--json"],
            cwd=Path.cwd(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_cli_subprocess_smoke_timeout(),
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        envelope = json.loads(result.stdout)
        payload = envelope.get("data", envelope)
        assert payload["kind"] == f"devtool.config-{action}"
        assert isinstance(payload["differences"], list)
        assert isinstance(payload["difference_count"], int)
    assert Path(".devtool.toml").read_bytes() == before


def test_legacy_run_log_migration_is_explicit_non_destructive_and_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    legacy = repo / ".devtool" / "logs"
    legacy.mkdir(parents=True)
    source = legacy / "old-run.log"
    source.write_text("historical output\n", encoding="utf-8")
    catalog_root = repo / ".devtool" / "private" / "run-v3"

    catalog = RunCatalog.open(catalog_root, create=False)
    assert not catalog_root.exists()
    result = migrate_legacy_run_storage(repo, catalog)
    assert result["mutated"] is True
    assert result["source_bytes_preserved"] is True
    assert source.read_text(encoding="utf-8") == "historical output\n"
    assert catalog_root.joinpath("catalog.json").is_file()

    reopened = RunCatalog.open(catalog_root, create=False)
    rows = reopened.query(RunQuery(attachment_kind="legacy-log"))
    assert len(rows) == 1
    assert rows[0]["run"]["status"] == "legacy"
    assert len(rows[0]["attachments"]) == 1

    migrate_legacy_run_storage(repo, reopened)
    again = RunCatalog.open(catalog_root, create=False)
    assert len(again.runs) == 1
    assert len(again.attachments) == 1


def test_declared_help_completion_and_query_budgets_are_enforced() -> None:
    started = time.perf_counter()
    parser = build_parser()
    assert (time.perf_counter() - started) * 1000 < PERFORMANCE_BUDGETS_MS["startup_parser"]

    bundle = build_generated_surfaces(parser)
    started = time.perf_counter()
    filtered = filter_machine_help(bundle.machine_help, search="config", include_aliases=True)
    assert filtered["count"] > 0
    assert (time.perf_counter() - started) * 1000 < PERFORMANCE_BUDGETS_MS["machine_help_filter"]

    started = time.perf_counter()
    for shell in ("zsh", "bash", "fish"):
        assert generated_completion(bundle.registry, shell)
    assert (time.perf_counter() - started) * 1000 < PERFORMANCE_BUDGETS_MS["completion_generation"]

    catalog = RunCatalog(root=Path("/nonexistent/read-only"))
    catalog.runs = {
        f"run-{index:05d}": {
            "run_id": f"run-{index:05d}",
            "command_id": "test.run",
            "status": "ok",
            "provider": "local",
            "created_at": float(index),
        }
        for index in range(10_000)
    }
    started = time.perf_counter()
    rows = catalog.query(RunQuery(command_id="test.run", limit=100))
    assert len(rows) == 100
    assert (time.perf_counter() - started) * 1000 < PERFORMANCE_BUDGETS_MS["run_query_10000"]


def test_storage_status_classifies_owned_families_and_fails_unknown_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".devtool" / "logs").mkdir(parents=True)
    (repo / ".devtool" / "transactions").mkdir(parents=True)
    catalog = RunCatalog.open(repo / ".devtool" / "private" / "run-v3", create=False)
    status = run_storage_status(repo, catalog)
    assert status["status"] == "pass"
    by_family = {row["family"]: row for row in status["inventory"]["paths"]}
    assert by_family["logs"]["classification"] == "legacy"
    assert by_family["transactions"]["owner"] == "LIFE"

    (repo / ".devtool" / "mystery-state").mkdir()
    failed = run_storage_status(repo, catalog)
    assert failed["status"] == "fail"
    assert failed["inventory"]["unclassified"][0]["family"] == "mystery-state"


def test_generated_help_option_index_preserves_full_canonical_order_and_metadata() -> None:
    """Indexed projection must agree with a deliberately slow independent oracle."""
    from devtool.core_surfaces import machine_help_payload

    parser = build_parser()
    registry = build_adopted_core_registry(parser)
    payload = machine_help_payload(parser, registry)
    assert payload["parser_parity"]["missing_count"] == 0
    assert payload["parser_parity"]["option_missing_count"] == 0
    assert payload["parser_parity"]["option_extra_count"] == 0
    for command in payload["commands"]:
        selected_path = tuple(command["path"])
        expected = []
        for option in registry.options:
            metadata = dict(option.metadata)
            if tuple(str(x) for x in (metadata.get("path") or ())) != selected_path:
                continue
            expected.append({
                "id": option.stable_id,
                "dest": str(metadata.get("dest") or ""),
                "spellings": list(option.all_spellings()),
                "required": bool(metadata.get("required")),
                "nargs": metadata.get("nargs"),
                "choices": list(metadata.get("choices") or ()),
                "help": "" if metadata.get("hidden") else str(metadata.get("help") or ""),
                "hidden": bool(metadata.get("hidden")),
            })
        assert command["options"] == expected, selected_path
