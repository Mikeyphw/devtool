from __future__ import annotations

import argparse
import io
from pathlib import Path

from rich.console import Console

from devtool import cli
from devtool.interactive.fzf import Choice, TerminalUI
from devtool.interactive.validation_catalog import ValidationCatalog, dependency_closure
from devtool.interactive.validation_menu import _main_choices, _main_header
from devtool.interactive.validation_recipes import ValidationRecipe, expand_recipe, load_recipes, save_recipe
from devtool.interactive.validation_wizard import ValidationDraft
from devtool.models import ProjectConfig


def test_complex_validation_draft_compiles_canonical_command() -> None:
    draft = ValidationDraft(
        repo="/repo",
        target="android_hud",
        chroot="androidos",
        method="sequential-continue",
        action="continue",
        validation_scope="selected",
        phases=["compile", "lint"],
        test_profile="official",
        tasks=[":app:testOfficialDebugUnitTest", ":app:lintOfficialDebug"],
        resource_profile="android-low-power",
        max_workers=1,
        cpu_limit=2,
        gradle_heap_mb=1536,
        memory_guard_mb=640,
        low_priority=True,
        parallel=False,
        build_cache=True,
        gradle_daemon=False,
        stop_below_battery=30,
        require_charging=True,
        stop_above_temperature=44,
        failure_action="pause",
    )

    argv = draft.full_argv()
    assert argv[:5] == ["devtool", "-r", "/repo", "--target", "android_hud"]
    assert ["--chroot", "androidos"] == argv[5:7]
    assert argv[7:9] == ["validate", "continue"]
    assert argv.count("--phase") == 2
    assert ":app:testOfficialDebugUnitTest" in argv
    assert "--max-workers" in argv
    assert "--no-parallel" in argv
    assert "--no-daemon" in argv
    assert "--require-charging" in argv
    assert draft.validate() == []


def test_deferred_apply_rejects_phase_selection() -> None:
    draft = ValidationDraft(method="apply", artifacts=["patch.zip"], apply_mode="deferred", phases=["compile"])
    assert any("phase selection" in item.lower() for item in draft.validate())


def test_dependency_closure_includes_prerequisites_and_satisfied_phases() -> None:
    phases = [
        {"key": "a", "name": "preflight", "status": "passed", "depends_on": []},
        {"key": "b", "name": "compile", "status": "pending", "depends_on": ["a"]},
        {"key": "c", "name": "lint", "status": "pending", "depends_on": ["b"]},
    ]
    closure, satisfied = dependency_closure(phases, ["lint"])
    assert closure == ["preflight", "compile", "lint"]
    assert satisfied == ["preflight"]


def test_recipe_round_trip(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    path = save_recipe(
        repo,
        name="phone-safe",
        label="Phone safe",
        description="Constrained validation",
        argv=["validate", "--resource-profile", "android-low-power"],
        repository=True,
    )
    assert path.is_file()
    recipe = next(item for item in load_recipes(repo) if item.name == "phone-safe")
    assert recipe.argv == ("validate", "--resource-profile", "android-low-power")


def test_recipe_placeholder_expansion() -> None:
    recipe = ValidationRecipe("overlay", "Overlay", "", ("validate", "overlay", "{artifact}"))
    assert expand_recipe(recipe, artifact="demo.zip") == ["validate", "overlay", "demo.zip"]


def test_matrix_recipe_placeholder_expansion() -> None:
    recipe = ValidationRecipe("matrix", "Matrix", "", ("validate", "--matrix", "{matrix}", "--matrix-mode", "target"))
    assert expand_recipe(recipe, matrix="app,android_hud") == [
        "validate", "--matrix", "app,android_hud", "--matrix-mode", "target"
    ]


def test_parser_supports_menu_aliases_and_resource_overrides() -> None:
    parser = cli.build_parser()
    direct = parser.parse_args(["validation-menu", "--recipe", "smart"])
    alias = parser.parse_args(["vm", "--recipe", "smart"])
    nested = parser.parse_args(["menu", "validation", "--wizard", "--matrix-values", "app,hud"])
    validate = parser.parse_args([
        "validate", "--max-workers", "1", "--cpu-limit", "2", "--gradle-heap-mb", "1536",
        "--memory-guard-mb", "0", "--low-priority", "--no-parallel", "--build-cache", "--no-daemon",
    ])
    cli._normalize_command_aliases(alias)
    cli._normalize_command_aliases(nested)
    assert direct.command == "validation-menu"
    assert alias.command == "validation-menu"
    assert nested.command == "validation-menu"
    assert nested.matrix_values == "app,hud"
    assert validate.max_workers == 1
    assert validate.cpu_limit == 2
    assert validate.gradle_heap_mb == 1536
    assert validate.memory_guard_mb == 0
    assert validate.validation_low_priority is True
    assert validate.validation_parallel is False
    assert validate.validation_build_cache is True
    assert validate.validation_daemon is False


def test_resource_overrides_are_ephemeral_config_values(tmp_path: Path) -> None:
    config = ProjectConfig(repo_root=tmp_path, kind="android", runner_options={})
    args = argparse.Namespace(
        command="validate",
        validation_resource_profile="automatic",
        max_workers=1,
        cpu_limit=2,
        gradle_heap_mb=1536,
        memory_guard_mb=0,
        validation_low_priority=True,
        validation_parallel=False,
        validation_build_cache=False,
        validation_daemon=False,
        _devtool_passthrough_args=[],
        profile="",
        test_profile="",
        select=[],
        test_ids=[],
        tag=[],
        exclude_tag=[],
        test_paths=[],
        test_classes=[],
        test_methods=[],
        test_filter="",
        test_task=[],
        changed_from="HEAD",
        failed=False,
        changed=False,
        instrumented=False,
    )
    cli._apply_runner_cli_options(config, args)
    validation = config.runner_options["validation"]
    gradle = config.runner_options["gradle"]
    assert validation["max_workers"] == 1
    assert validation["cpu_limit"] == 2
    assert validation["gradle_heap_mb"] == 1536
    assert validation["memory_guard_mb"] == 0
    assert validation["low_priority"] is True
    assert validation["parallel"] is False
    assert validation["no_daemon"] is True
    assert gradle["build_cache"] is False
    assert gradle["no_daemon"] is True


def test_noninteractive_recipe_print_command(tmp_path: Path, capsys) -> None:
    (tmp_path / "pyproject.toml").write_text("[build-system]\nrequires=[]\nbuild-backend='setuptools.build_meta'\n", encoding="utf-8")
    rc = cli.main(["-r", str(tmp_path), "validation-menu", "--recipe", "smart", "--print-command"])
    assert rc == 0
    output = capsys.readouterr().out.strip()
    assert output.endswith(" validate")
    assert str(tmp_path) in output

def test_state_aware_choices_include_safe_retry_plan_acceptance_and_logs(tmp_path: Path) -> None:
    config = ProjectConfig(repo_root=tmp_path, kind="android", runner_options={})
    catalog = ValidationCatalog(
        config=config,
        session={
            "session_id": "session-1",
            "revision": 7,
            "state": "recovery-required",
            "effective_state": "recovery-required",
            "recommended_retry_profile": "android-low-power",
        },
        plan={
            "phases": [
                {"key": "compile", "name": "compile", "label": "Compile", "status": "failed", "depends_on": []},
            ]
        },
    )
    keys = {item.key for item in _main_choices(catalog)}
    assert {"retry", "safe-retry", "recover", "accept-plan-change", "logs"} <= keys


def test_termux_fzf_uses_bottom_preview_and_ascii_markers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TERMUX_VERSION", "0.119")
    monkeypatch.setenv("COLUMNS", "64")
    ui = TerminalUI(console=Console(file=io.StringIO(), force_terminal=True, width=64))
    ui.fzf = "/usr/bin/fzf"
    monkeypatch.setattr(TerminalUI, "interactive", property(lambda _self: True))
    captured: dict[str, list[str]] = {}

    def fake_run(argv, **_kwargs):
        captured["argv"] = list(argv)
        return argparse.Namespace(returncode=130, stdout="")

    monkeypatch.setattr("devtool.interactive.fzf.subprocess.run", fake_run)

    ui.choose([Choice("go", "▶ Continue", "Run remaining → done")], prompt="validation", header="repo · target")

    assert "--height=100%" in captured["argv"]
    assert "--preview-window=down,45%,border-top,wrap" in captured["argv"]
    assert "--pointer=>" in captured["argv"]
    assert "--marker=*" in captured["argv"]
    assert "--header=repo - target" in captured["argv"]


def test_narrow_fallback_prints_description_on_separate_line(monkeypatch) -> None:
    stream = io.StringIO()
    ui = TerminalUI(console=Console(file=stream, force_terminal=False, width=56), force_fallback=True)
    monkeypatch.setattr("devtool.interactive.fzf.Prompt.ask", lambda *_args, **_kwargs: "0")

    ui.choose([Choice("go", "Run", "A long description that should not share the numbered row")], prompt="validation", header="Header")

    output = stream.getvalue()
    assert "  1  Run\n" in output
    assert "      A long description" in output


def test_narrow_validation_header_uses_repo_name(monkeypatch, tmp_path: Path) -> None:
    import devtool.interactive.validation_menu as validation_menu

    monkeypatch.setenv("TERMUX_VERSION", "0.119")
    stream = io.StringIO()
    monkeypatch.setattr(validation_menu, "console", Console(file=stream, force_terminal=True, width=64))
    root = tmp_path / "very" / "deep" / "repo-name"
    config = ProjectConfig(repo_root=root, kind="android", selected_target="app")
    catalog = ValidationCatalog(config=config)

    header = _main_header(catalog)

    assert str(root) not in header
    assert "repo-name" in header
    assert "session none" in header


def test_noninteractive_vm_alias_prints_recipe_command(tmp_path: Path, capsys) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires=[]\nbuild-backend='setuptools.build_meta'\n",
        encoding="utf-8",
    )
    rc = cli.main(["-r", str(tmp_path), "vm", "--recipe", "smart", "--print-command"])
    assert rc == 0
    assert capsys.readouterr().out.strip().endswith(" validate")


def test_noninteractive_target_matrix_recipe(tmp_path: Path, capsys) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires=[]\nbuild-backend='setuptools.build_meta'\n",
        encoding="utf-8",
    )
    rc = cli.main([
        "-r", str(tmp_path), "validation-menu", "--recipe", "target-matrix",
        "--matrix-values", "core,android_hud", "--print-command",
    ])
    assert rc == 0
    output = capsys.readouterr().out.strip()
    assert "--matrix core,android_hud --matrix-mode target" in output



def test_sequential_commands_are_revision_guarded() -> None:
    for action in ("continue", "retry"):
        draft = ValidationDraft(
            method="sequential-continue",
            action=action,
            session_id="session-7",
            revision=41,
            validation_scope="selected",
            phases=["compile", "lint"],
        )
        argv = draft.command_argv()
        assert argv[:2] == ["validate", action]
        assert argv[argv.index("--if-revision") + 1] == "41"
        assert argv[argv.index("--session") + 1] == "session-7"


def test_sequential_json_output_and_machine_format() -> None:
    draft = ValidationDraft(
        method="sequential-continue",
        action="continue",
        session_id="session-json",
        revision=8,
        json_output=True,
        machine_format="compact",
    )
    argv = draft.command_argv()
    assert "--json" in argv
    assert argv[argv.index("--machine") + 1] == "compact"
    assert draft.validate() == []

    ordinary = ValidationDraft(method="validate", json_output=True)
    assert any("resumable validation" in item for item in ordinary.validate())


def test_recipe_round_trip_preserves_global_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    prefix = [
        "-r", str(repo), "--target", "android_hud", "--chroot", "androidos",
        "--copy", "--auto-hud", "--hud-mode", "android-bridge", "--hud-overlay",
    ]
    save_recipe(
        repo,
        name="contextual",
        label="Contextual",
        description="Preserves invocation context",
        argv=["validate", "continue", "--scope", "next"],
        prefix_argv=prefix,
        repository=True,
    )
    recipe = next(item for item in load_recipes(repo) if item.name == "contextual")
    assert list(recipe.prefix_argv) == prefix
    draft = ValidationDraft(raw_override=list(recipe.argv), global_override=list(recipe.prefix_argv))
    command = draft.full_argv()
    assert command.count("--hud-overlay") == 1
    assert command[:4] == ["devtool", "-r", str(repo), "--target"]


def test_fzf_expected_refresh_action_survives_without_selected_row(monkeypatch, tmp_path: Path) -> None:
    from subprocess import CompletedProcess

    from devtool.interactive.fzf import Choice, TerminalUI

    ui = TerminalUI(force_fallback=False)
    ui.fzf = "/usr/bin/fzf"
    monkeypatch.setattr(
        "devtool.interactive.fzf.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, stdout="ctrl-r\n", stderr=""),
    )
    selection = ui._choose_fzf(
        [Choice("one", "One")],
        prompt="test",
        header="",
        multi=False,
        expect=("ctrl-r",),
        initial_query="",
    )
    assert selection is not None
    assert selection.action == "ctrl-r"
    assert selection.keys == ()


def test_catalog_previews_real_runner_plan_without_session(tmp_path: Path) -> None:
    from devtool.interactive.validation_catalog import build_catalog

    config = ProjectConfig(
        repo_root=tmp_path,
        kind="command",
        restore_command="echo restore",
        build_command="echo build",
        test_command="echo test",
        runner_options={},
    )
    catalog = build_catalog(config)
    assert catalog.plan.get("preview") is True
    assert catalog.plan_error == ""
    names = [str(item.get("name")) for item in catalog.phases]
    assert names == ["preflight", "package", "unit-test"]
    assert "compile" not in names
    assert "lint" not in names


def test_reset_from_phase_generates_control_boundary(tmp_path: Path) -> None:
    from devtool.interactive.fzf import Selection
    from devtool.interactive.validation_menu import _configure_reset

    class FakeUI:
        def __init__(self) -> None:
            self.values = iter((Selection(("from",)), Selection(("unit-test",))))

        def choose(self, *args, **kwargs):
            return next(self.values)

    config = ProjectConfig(repo_root=tmp_path, kind="android", runner_options={})
    catalog = ValidationCatalog(
        config=config,
        session={"session_id": "session-reset", "revision": 5},
        plan={"phases": [{"key": "unit-test", "name": "unit-test", "label": "Unit test", "status": "failed"}]},
    )
    draft = ValidationDraft(method="control", action="reset", session_id="session-reset", revision=5)
    assert _configure_reset(FakeUI(), draft, catalog) is True
    assert draft.from_phase == "unit-test"
    argv = draft.command_argv()
    assert argv[argv.index("--from-phase") + 1] == "unit-test"
    assert argv[argv.index("--if-revision") + 1] == "5"


def test_safe_retry_uses_supported_recommended_profile(tmp_path: Path) -> None:
    from devtool.interactive.validation_menu import _preview_safe_retry, _recommended_retry_profile

    config = ProjectConfig(repo_root=tmp_path, kind="android", runner_options={})
    catalog = ValidationCatalog(
        config=config,
        session={"revision": 9, "resource_profile": "android-low-power", "recommended_retry_profile": "standard"},
    )
    assert _recommended_retry_profile(catalog) == "standard"
    preview = _preview_safe_retry(catalog, "standard")
    assert "Current profile: `android-low-power`" in preview
    assert "Recommended profile: `standard`" in preview
    assert "revision guard: `9`" in preview


def test_fallback_dashboard_is_navigable_in_a_pseudo_tty(tmp_path: Path) -> None:
    import os
    import sys
    import warnings

    import pexpect

    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires=[]\nbuild-backend='setuptools.build_meta'\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        child = pexpect.spawn(
            sys.executable,
            ["-m", "devtool", "-r", str(tmp_path), "validation-menu", "--no-fzf"],
            env=env,
            encoding="utf-8",
            timeout=15,
        )
    child.expect("validation")
    child.sendline("0")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0


def test_main_menu_exposes_refresh_for_numbered_fallback(tmp_path: Path) -> None:
    config = ProjectConfig(repo_root=tmp_path, kind="command", build_command="echo build", runner_options={})
    catalog = ValidationCatalog(config=config)
    assert "refresh" in {choice.key for choice in _main_choices(catalog)}


def test_deprecated_validation_profile_alias_warns(capsys) -> None:
    cli._warn_deprecated_validation_profile_alias(
        argparse.Namespace(validation_resource_profile="standard"),
        ["validate", "--profile", "standard"],
    )
    captured = capsys.readouterr()
    assert "deprecated" in (captured.err + captured.out).lower()
    assert "--resource-profile" in (captured.err + captured.out)


def test_dashboard_ctrl_r_calls_real_catalog_refresh(monkeypatch, tmp_path: Path) -> None:
    from devtool.interactive import validation_menu
    from devtool.interactive.fzf import Selection

    config = ProjectConfig(repo_root=tmp_path, kind="command", build_command="echo build", runner_options={})
    catalog = ValidationCatalog(config=config, plan={"preview": True, "phases": []})

    class FakeUI:
        REVIEW_EXPECT = ()

        def __init__(self, **kwargs) -> None:
            self.interactive = True
            self.last_action = "enter"
            self.calls = 0

        def choose(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                self.last_action = "ctrl-r"
                return Selection((), action="ctrl-r")
            self.last_action = "enter"
            return Selection(("quit",))

        def page(self, text: str) -> None:
            raise AssertionError(text)

    refreshed: list[dict] = []

    def fake_refresh(current, **kwargs):
        refreshed.append(kwargs)
        current.session = {"revision": 22}
        return current

    monkeypatch.setattr(validation_menu, "TerminalUI", FakeUI)
    monkeypatch.setattr(validation_menu, "build_catalog", lambda *args, **kwargs: catalog)
    monkeypatch.setattr(validation_menu, "refresh_catalog", fake_refresh)
    args = argparse.Namespace(
        no_fzf=False,
        runtime_tasks=False,
        repo=str(tmp_path),
        config="",
        target="",
        variant="",
        solution="",
        project="",
        project_type="auto",
        execution_environment="auto",
        chroot="",
        host=False,
        no_auto_chroot=False,
        command="validation-menu",
        recipe="",
        wizard=False,
        print_command=False,
        _devtool_hud_explicit=False,
    )
    assert validation_menu.run_validation_menu(config, args) == 0
    assert len(refreshed) == 1
    assert refreshed[0]["repo"] == str(tmp_path)


def test_numbered_fallback_completes_full_guided_wizard(tmp_path: Path) -> None:
    import os
    import sys
    import warnings

    import pexpect

    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires=[]\nbuild-backend='setuptools.build_meta'\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        child = pexpect.spawn(
            sys.executable,
            ["-m", "devtool", "-r", str(tmp_path), "validation-menu", "--wizard", "--no-fzf"],
            env=env,
            encoding="utf-8",
            timeout=20,
        )
    for prompt, answer in (
        ("method", "1"),
        ("context", "9"),
        ("pipeline", "1"),
        ("tests", "11"),
        ("resources", "7"),
        ("output", "9"),
        ("review", "8"),
    ):
        child.expect(prompt)
        child.sendline(answer)
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0


def test_termux_chroot_command_round_trips_through_cli_parser() -> None:
    draft = ValidationDraft(
        repo="/data/data/com.termux/files/home/Code/app",
        target="android_hud",
        chroot="androidos",
        method="sequential-continue",
        action="continue",
        validation_scope="through",
        through="package",
        session_id="session-phone",
        revision=33,
        resource_profile="android-low-power",
    )
    parser = cli.build_parser()
    parsed = parser.parse_args(draft.full_argv()[1:])
    cli._normalize_command_aliases(parsed)
    assert parsed.repo == "/data/data/com.termux/files/home/Code/app"
    assert parsed.target == "android_hud"
    assert parsed.chroot == "androidos"
    assert parsed.command == "validate"
    assert parsed._devtool_validation_api_action == "run"
    assert parsed.if_revision == 33
    assert parsed.through == "package"
