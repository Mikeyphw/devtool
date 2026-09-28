from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from devtool.environment_composition import build_environment_composition_plan, apply_composition_to_spec
from devtool.go_environment import (
    GoEnvironmentRequest,
    destination_go_environment,
    destination_go_output_dir,
    packages_for_changed_paths,
    resolve_go_environment,
)
from devtool.go_test import GoTestJSONStream
from devtool.models import CommandSpec, ProjectConfig
from devtool.python_environment import (
    PythonEnvironmentRequest,
    destination_python_environment_root,
    resolve_python_environment,
)
from devtool.python_support import PythonProjectError, PythonProjectInfo, inspect_python_project, run_validator
from devtool.runners import build_specs
from devtool.ssh_execution import ensure_remote_toolchain
from devtool.ssh_tmux_runner import SSHRunnerConfig, _translate_env_for_repo
from devtool.ssh_worker import SSHWorkerProbe
from devtool.toolchains import apply_toolchain_wrapper, should_use_mise


def _exe(path: Path, text: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _worker(*, go: str = "") -> SSHWorkerProbe:
    tools = {"sh": "/bin/sh", "python3": "/usr/bin/python3", "git": "/usr/bin/git", "tar": "/bin/tar"}
    if go:
        tools["go"] = go
    return SSHWorkerProbe(
        profile="tablet", destination="mike@host", port=8022, platform="termux", os_name="Android",
        architecture="aarch64", cpu_count=8, memory_kib=8_000_000, free_storage_kib=10_000_000,
        home="/data/data/com.termux/files/home", prefix="/data/data/com.termux/files/usr", shell="/bin/sh",
        package_manager="pkg", tools=tools, versions={"go": "go1.24.1" if go else ""},
        bootstrap_policy="missing", tmux_required=False,
    )


def test_composition_order_is_host_direnv_mise_language(tmp_path: Path) -> None:
    (tmp_path / ".envrc").write_text("export DEMO=1\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    _exe(bin_dir / "direnv")
    _exe(bin_dir / "mise")
    env = {
        "PATH": str(bin_dir), "DEVTOOL_DIRENV": "on", "DEVTOOL_DIRENV_AUTHORIZED": "1",
        "DIRENV_DIR": str(tmp_path),
    }
    plan = build_environment_composition_plan(tmp_path, use_mise=True, language_provider="venv", env=env)
    assert [layer.role for layer in plan.layers] == ["host-base", "environment-overlay", "toolchain-provider", "language-environment"]
    assert plan.direnv.active and plan.mise.selected


def test_direnv_presence_does_not_authorize_itself(tmp_path: Path) -> None:
    (tmp_path / ".envrc").write_text("export SECRET=x\n", encoding="utf-8")
    # A marker alone is not an authorization request.  In auto mode, direnv is
    # optional when the provider is unavailable.
    plan = build_environment_composition_plan(tmp_path, use_mise=False, language_provider="python", env={"PATH": ""})
    assert plan.direnv.present
    assert not plan.direnv.requested
    assert not plan.direnv.authorized
    assert not plan.direnv.active
    plan.require_valid()


def test_direnv_requested_without_external_authorization_fails_closed(tmp_path: Path) -> None:
    (tmp_path / ".envrc").write_text("export DEMO=1\n", encoding="utf-8")
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, language_provider="python",
        env={"PATH": os.environ["PATH"], "DEVTOOL_DIRENV": "on"},
    )
    with pytest.raises(ValueError, match="not explicitly authorized"):
        plan.require_valid()


def test_direnv_virtualenv_conflicts_with_python_owner(tmp_path: Path) -> None:
    (tmp_path / ".envrc").write_text("export VIRTUAL_ENV=x\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir(); _exe(bin_dir / "direnv")
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, language_provider="poetry",
        env={"PATH": str(bin_dir), "DEVTOOL_DIRENV": "on", "DEVTOOL_DIRENV_AUTHORIZED": "1", "DIRENV_DIR": str(tmp_path), "VIRTUAL_ENV": "/tmp/foreign"},
    )
    with pytest.raises(ValueError, match="ownership conflict"):
        plan.require_valid()


def test_mise_qualification_disables_auto_install_and_resolves_exact_tool(tmp_path: Path) -> None:
    (tmp_path / "mise.toml").write_text('[tools]\npython="3.12"\n', encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    python = _exe(bin_dir / "python", "#!/bin/sh\nexit 0\n")
    mise = _exe(bin_dir / "mise", f'#!/bin/sh\n[ "$1" = which ] && {{ echo "{python}"; exit 0; }}\nexit 0\n')
    plan = build_environment_composition_plan(tmp_path, use_mise=True, language_provider="venv", env={"PATH": str(bin_dir)})
    assert plan.mise.executable.path == str(mise)
    assert dict(plan.mise.resolved_tools)["python"] == str(python)
    spec = apply_composition_to_spec(CommandSpec("test", ["pytest"], env={"PATH": str(bin_dir)}, cwd=str(tmp_path)), plan)
    assert [Path(spec.argv[0]).name, *spec.argv[1:3]] == ["mise", "exec", "--"]
    assert spec.env["MISE_PARANOID"] == "1"
    assert spec.env["MISE_EXEC_AUTO_INSTALL"] == "0"


def test_mise_provider_path_change_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "mise.toml").write_text('[tools]\npython="3.12"\n', encoding="utf-8")
    a = tmp_path / "a"; b = tmp_path / "b"; a.mkdir(); b.mkdir()
    _exe(a / "mise"); _exe(b / "mise")
    plan = build_environment_composition_plan(tmp_path, use_mise=True, language_provider="python", env={"PATH": str(a)})
    with pytest.raises(ValueError, match="provider changed"):
        apply_composition_to_spec(CommandSpec("test", ["pytest"], env={"PATH": str(b)}, cwd=str(tmp_path)), plan)


def test_tool_versions_alone_is_not_auto_mise_ownership(tmp_path: Path) -> None:
    (tmp_path / ".tool-versions").write_text("python 3.12\n", encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, kind="python", project_root=str(tmp_path))
    with patch("devtool.toolchains.shutil.which", return_value="/usr/bin/mise"):
        assert not should_use_mise(config, cwd=str(tmp_path), env={"PATH": os.environ["PATH"]})
    config.toolchain_manager = "mise"
    with patch("devtool.toolchains.shutil.which", return_value="/usr/bin/mise"):
        assert should_use_mise(config, cwd=str(tmp_path), env={"PATH": os.environ["PATH"]})


def test_requirements_only_project_does_not_auto_select_uv(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    with patch("devtool.python_support.shutil.which", return_value="/usr/bin/uv"):
        info = inspect_python_project(tmp_path, "auto", "auto")
    assert info.manager == "venv"


def test_explicit_python_environment_cannot_be_ignored_by_poetry(tmp_path: Path) -> None:
    info = PythonProjectInfo(tmp_path, "poetry", "pytest", "", "demo", "poetry.lock")
    request = PythonEnvironmentRequest(root=str(tmp_path), manager="poetry", environment=".venv")
    with pytest.raises(PythonProjectError, match="cannot be combined"):
        resolve_python_environment(info, request, env={"PATH": os.environ["PATH"]})


def test_poetry_receipt_uses_actual_manager_environment(tmp_path: Path) -> None:
    (tmp_path / "poetry.lock").write_text("# lock\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    py = Path(os.sys.executable)
    envroot = tmp_path / ".poetry-env"; envroot.mkdir()
    poetry = _exe(bin_dir / "poetry", f'''#!/bin/sh\nif [ "$1 $2 $3" = "env info --executable" ]; then echo "{py}"; exit 0; fi\nif [ "$1 $2 $3" = "env info --path" ]; then echo "{envroot}"; exit 0; fi\nexit 1\n''')
    info = PythonProjectInfo(tmp_path, "poetry", "pytest", "", "demo", "poetry.lock")
    request = PythonEnvironmentRequest(root=str(tmp_path), manager="poetry", configured_python=str(py), configured_python_explicit=True)
    resolved = resolve_python_environment(info, request, env={"PATH": str(bin_dir)})
    assert resolved.manager_receipt.executable == str(poetry)
    assert resolved.python == str(py)
    assert resolved.environment_root == str(envroot)
    assert resolved.ownership == "manager-owned"


def test_lock_require_fails_without_recognized_lock(tmp_path: Path) -> None:
    info = PythonProjectInfo(tmp_path, "venv", "pytest", "", "demo", "")
    request = PythonEnvironmentRequest(root=str(tmp_path), manager="venv", lock_policy="require")
    with pytest.raises(PythonProjectError, match="no recognized lock file"):
        resolve_python_environment(info, request, env={"PATH": os.environ["PATH"]})


def test_destination_python_environment_is_repo_relative() -> None:
    path = destination_python_environment_root(Path("/home/controller/repo"), "ssh", "app")
    assert path == ".devtool/environments/python/ssh/app"
    assert "/home/controller" not in path


def test_python_spec_evidence_names_resolved_environment(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="demo"\nversion="1"\n', encoding="utf-8")
    (tmp_path / "tests").mkdir(); (tmp_path / "tests/test_demo.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, kind="python", project_root=str(tmp_path), toolchain_manager="none")
    spec = build_specs(config, "test")[0]
    assert spec.metadata["python_environment_identity"]
    assert spec.metadata["environment_domains"]["python"] == spec.metadata["python_environment_identity"]


def test_go_environment_separates_launcher_effective_toolchain_and_redacts_auth(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\ngo 1.23\ntoolchain go1.24.1\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    payload = {"GOTOOLCHAIN":"auto","GOWORK":"off","GOVERSION":"go1.24.1","GOROOT":"/go","GOMOD":str(tmp_path/"go.mod"),"GOCACHE":"/cache","GOMODCACHE":"/mod","GOAUTH":"top-secret"}
    go = _exe(bin_dir / "go", '#!/usr/bin/env python3\nimport json,sys\n' + f'P={payload!r}\n' + 'print(json.dumps(P) if len(sys.argv)>1 and sys.argv[1]=="env" else "go version go1.24.1 linux/amd64")\n')
    resolved = resolve_go_environment(GoEnvironmentRequest(str(tmp_path), "test", launcher="go"), env={"PATH": str(bin_dir), "GOAUTH":"top-secret"})
    assert resolved.launcher == str(go)
    assert resolved.effective_toolchain == "go1.24.1"
    assert dict(resolved.environment)["GOAUTH"].startswith("sha256:")
    assert "top-secret" not in json.dumps(resolved.to_dict())


def test_go_changed_path_selects_package_not_test_regex(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\ngo 1.23\n", encoding="utf-8")
    pkg = tmp_path / "pkg"; pkg.mkdir(); (pkg / "x.go").write_text("package pkg\nfunc X(){}\n", encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, kind="go", project_root=str(tmp_path), toolchain_manager="none", runner_options={"test_selection":{"paths":["pkg/x.go"]}})
    spec = build_specs(config, "test")[0]
    assert "-run" not in spec.argv
    assert "./pkg" in spec.argv
    assert spec.metadata["go_selection_mode"] == "package-path"


def test_go_unsupported_tag_selection_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\ngo 1.23\n", encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, kind="go", project_root=str(tmp_path), toolchain_manager="none", runner_options={"test_selection":{"tags":["unit"]}})
    with pytest.raises(SystemExit, match="cannot safely translate"):
        build_specs(config, "test")


def test_go_build_fail_events_are_not_lost() -> None:
    stream = GoTestJSONStream()
    out = stream.feed(json.dumps({"Action":"build-output","ImportPath":"example.com/demo/pkg","Output":"pkg/x.go:9: undefined: Nope\n"})+"\n")
    out += stream.feed(json.dumps({"Action":"build-fail","ImportPath":"example.com/demo/pkg"})+"\n")
    report = stream.report()
    assert "undefined: Nope" in out
    assert report.packages_failed == 1
    assert report.failures and "undefined: Nope" in report.failures[0].message


def test_go_failedbuild_without_package_is_preserved() -> None:
    stream = GoTestJSONStream()
    stream.feed(json.dumps({"Action":"fail","FailedBuild":"example.com/demo/pkg"})+"\n")
    assert stream.report().packages_failed == 1


def test_destination_go_paths_are_destination_owned(tmp_path: Path) -> None:
    env = destination_go_environment(tmp_path, "ssh")
    assert env["GOCACHE"].startswith(".devtool/cache/go/ssh/")
    assert env["GOMODCACHE"].startswith(".devtool/cache/go/ssh/")
    assert destination_go_output_dir("build", "ssh") == ".devtool/build/go"


def test_ssh_drops_host_owned_python_and_go_paths(tmp_path: Path) -> None:
    translated = _translate_env_for_repo(
        {"UV_PROJECT_ENVIRONMENT":"/home/controller/.venv","GOCACHE":"/home/controller/gocache","GOMODCACHE":"/home/controller/mod","KEEP":"yes"},
        tmp_path, "/remote/repo",
    )
    assert "UV_PROJECT_ENVIRONMENT" not in translated
    assert "GOCACHE" not in translated and "GOMODCACHE" not in translated
    assert translated["KEEP"] == "yes"


def test_ssh_go_toolchain_is_fail_closed_when_missing() -> None:
    config = SSHRunnerConfig(host="host")
    spec = CommandSpec("test", ["go","test","./..."], metadata={"runner":"go"})
    with pytest.raises(ValueError, match="does not provide a usable go"):
        ensure_remote_toolchain(config, [spec], worker=_worker(), dry_run=False)
    result = ensure_remote_toolchain(config, [spec], worker=_worker(go="/data/data/com.termux/files/usr/bin/go"), dry_run=False)
    assert result["ok"] and result["kind"] == "go"


def test_composition_wrapper_preserves_language_environment_domains(tmp_path: Path) -> None:
    (tmp_path / "mise.toml").write_text('[tools]\ngo="1.24"\n', encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir(); _exe(bin_dir / "mise")
    config = ProjectConfig(repo_root=tmp_path, kind="go", project_root=str(tmp_path), toolchain_manager="auto")
    spec = CommandSpec("test", ["go","test","./..."], env={"PATH":str(bin_dir)}, cwd=str(tmp_path), metadata={"environment_domains":{"go":"go-id"}})
    wrapped = apply_toolchain_wrapper(config, [spec], env={"PATH":str(bin_dir)})[0]
    assert wrapped.metadata["environment_domains"]["go"] == "go-id"
    assert wrapped.metadata["environment_domains"]["composition"]


def test_python_audit_closes_extensionless_script_handles(tmp_path: Path) -> None:
    import gc
    import warnings
    from devtool.python_support import _python_audit_files

    tool = tmp_path / "tool"
    tool.write_text("#!/usr/bin/env python3\nprint('ok')\n", encoding="utf-8")
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always", ResourceWarning)
        files = _python_audit_files(tmp_path)
        gc.collect()
    assert tool in files
    assert not [item for item in seen if issubclass(item.category, ResourceWarning)]


def test_asdf_explicit_provider_uses_tool_versions_and_exec(tmp_path: Path) -> None:
    from devtool.toolchains import apply_toolchain_wrapper
    (tmp_path / ".tool-versions").write_text("python 3.12.7\ngo 1.24.1\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    python = _exe(bin_dir / "python")
    go = _exe(bin_dir / "go")
    asdf = _exe(bin_dir / "asdf", f'''#!/bin/sh
if [ "$1" = version ]; then echo "v0.18.0"; exit 0; fi
if [ "$1 $2 $3" = "plugin list --urls" ]; then echo "python https://github.com/danhper/asdf-python.git"; echo "go https://github.com/asdf-community/asdf-golang.git"; exit 0; fi
if [ "$1" = which ] && [ "$2" = python ]; then echo "{python}"; exit 0; fi
if [ "$1" = which ] && [ "$2" = go ]; then echo "{go}"; exit 0; fi
if [ "$1" = current ] && [ "$2" = python ]; then echo "python 3.12.7 {tmp_path}/.tool-versions"; exit 0; fi
if [ "$1" = current ] && [ "$2" = go ]; then echo "go 1.24.1 {tmp_path}/.tool-versions"; exit 0; fi
exit 0
''')
    config = ProjectConfig(repo_root=tmp_path, kind="go", project_root=str(tmp_path), toolchain_manager="asdf")
    spec = CommandSpec("test", ["go", "test", "./..."], env={"PATH": str(bin_dir)}, cwd=str(tmp_path))
    wrapped = apply_toolchain_wrapper(config, [spec], env={"PATH": str(bin_dir)})[0]
    assert Path(wrapped.argv[0]).name == "asdf"
    assert wrapped.argv[1:3] == ["exec", "go"]
    comp = wrapped.metadata["environment_composition"]
    assert comp["toolchain_provider"] == "asdf"
    assert comp["asdf"]["version"] == "v0.18.0"
    assert comp["asdf"]["resolved_versions"]["python"] == "3.12.7"
    assert comp["asdf"]["plugins"]["python"].endswith("asdf-python.git")
    assert wrapped.metadata["environment_domains"]["asdf"]
    assert wrapped.metadata["environment_domains"]["toolchain"]
    assert str(asdf) == comp["asdf"]["executable"]["path"]


def test_tool_versions_auto_selects_asdf_not_mise_when_asdf_available(tmp_path: Path) -> None:
    from devtool.toolchains import selected_toolchain_manager
    (tmp_path / ".tool-versions").write_text("python 3.12.7\n", encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, kind="python", project_root=str(tmp_path), toolchain_manager="auto")
    def which(name: str, path: str | None = None):
        return "/usr/bin/asdf" if name == "asdf" else ("/usr/bin/mise" if name == "mise" else None)
    with patch("devtool.toolchains.shutil.which", side_effect=which):
        assert selected_toolchain_manager(config, cwd=str(tmp_path), env={"PATH": "/usr/bin"}) == "asdf"
        assert not should_use_mise(config, cwd=str(tmp_path), env={"PATH": "/usr/bin"})


def test_mise_owned_marker_wins_auto_over_shared_asdf_marker(tmp_path: Path) -> None:
    from devtool.toolchains import selected_toolchain_manager
    (tmp_path / "mise.toml").write_text('[tools]\npython="3.12"\n', encoding="utf-8")
    (tmp_path / ".tool-versions").write_text("python 3.12.7\n", encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, kind="python", project_root=str(tmp_path), toolchain_manager="auto")
    with patch("devtool.toolchains.shutil.which", return_value="/usr/bin/provider"):
        assert selected_toolchain_manager(config, cwd=str(tmp_path), env={"PATH": "/usr/bin"}) == "mise"


def test_asdf_provider_path_change_fails_closed(tmp_path: Path) -> None:
    (tmp_path / ".tool-versions").write_text("python 3.12.7\n", encoding="utf-8")
    a = tmp_path / "a"; b = tmp_path / "b"; a.mkdir(); b.mkdir()
    _exe(a / "asdf", f'''#!/bin/sh
[ "$1" = version ] && echo v0.18.0
[ "$1" = current ] && echo "python 3.12.7 {tmp_path}/.tool-versions"
exit 0
''')
    _exe(b / "asdf", f'''#!/bin/sh
[ "$1" = version ] && echo v0.18.0
[ "$1" = current ] && echo "python 3.12.7 {tmp_path}/.tool-versions"
exit 0
''')
    plan = build_environment_composition_plan(tmp_path, use_mise=False, use_asdf=True, language_provider="python", env={"PATH": str(a)})
    with pytest.raises(ValueError, match="asdf provider changed"):
        apply_composition_to_spec(CommandSpec("test", ["python", "-V"], env={"PATH": str(b)}, cwd=str(tmp_path)), plan)


def test_direnv_passive_status_authorizes_without_devtool_override(tmp_path: Path) -> None:
    (tmp_path / ".envrc").write_text("export DEMO=1\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    _exe(bin_dir / "direnv", '''#!/bin/sh
if [ "$1 $2" = "status --json" ]; then printf '%s\n' '{"state":{"foundRC":{"status":{"text":"ALLOWED"},"watch":[".envrc"]}},"config":{"direnvrc":"user"}}'; exit 0; fi
if [ "$1 $2" = "export json" ]; then printf '%s\n' '{"DEMO":"1","API_TOKEN":"sekret"}'; exit 0; fi
if [ "$1" = version ]; then echo 2.37.1; exit 0; fi
exit 0
''')
    plan = build_environment_composition_plan(tmp_path, use_mise=False, language_provider="python", env={"PATH": str(bin_dir)})
    assert plan.direnv.authorized and plan.direnv.active
    assert plan.direnv.status == "allowed"
    assert set(plan.direnv.changed_names) == {"API_TOKEN", "DEMO"}
    assert plan.direnv.secret_dependent
    assert not plan.direnv.portable
    payload = json.dumps(plan.direnv.to_dict())
    assert "sekret" not in payload
    assert plan.direnv.overlay_identity


def test_direnv_provider_path_change_fails_closed(tmp_path: Path) -> None:
    (tmp_path / ".envrc").write_text("export DEMO=1\n", encoding="utf-8")
    a = tmp_path / "a"; b = tmp_path / "b"; a.mkdir(); b.mkdir()
    script = '''#!/bin/sh
if [ "$1 $2" = "status --json" ]; then echo '{"state":{"foundRC":{"status":{"text":"ALLOWED"}}}}'; exit 0; fi
if [ "$1 $2" = "export json" ]; then echo '{"DEMO":"1"}'; exit 0; fi
exit 0
'''
    _exe(a / "direnv", script); _exe(b / "direnv", script)
    plan = build_environment_composition_plan(tmp_path, use_mise=False, language_provider="python", env={"PATH": str(a)})
    with pytest.raises(ValueError, match="direnv provider changed"):
        apply_composition_to_spec(CommandSpec("test", ["python", "-V"], env={"PATH": str(b)}, cwd=str(tmp_path)), plan)


def test_direnv_asdf_version_override_conflicts_before_execution(tmp_path: Path) -> None:
    (tmp_path / ".envrc").write_text("export ASDF_PYTHON_VERSION=3.11\n", encoding="utf-8")
    (tmp_path / ".tool-versions").write_text("python 3.12.7\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    _exe(bin_dir / "asdf", '#!/bin/sh\n[ "$1" = version ] && echo v0.18.0\nexit 0\n')
    _exe(bin_dir / "direnv", '''#!/bin/sh
if [ "$1 $2" = "status --json" ]; then echo '{"state":{"foundRC":{"status":{"text":"ALLOWED"}}}}'; exit 0; fi
if [ "$1 $2" = "export json" ]; then echo '{"ASDF_PYTHON_VERSION":"3.11"}'; exit 0; fi
exit 0
''')
    plan = build_environment_composition_plan(tmp_path, use_mise=False, use_asdf=True, language_provider="venv", env={"PATH": str(bin_dir)})
    with pytest.raises(ValueError, match=r"ASDF_\*_VERSION"):
        plan.require_valid()


def test_mise_receipt_captures_version_trust_exact_version_backend(tmp_path: Path) -> None:
    (tmp_path / "mise.toml").write_text('[tools]\npython="3.12"\n', encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir(); python = _exe(bin_dir / "python")
    _exe(bin_dir / "mise", f'''#!/bin/sh
if [ "$1 $2" = "version --json" ]; then echo '{{"version":"2026.9.1"}}'; exit 0; fi
if [ "$1 $2" = "trust --show" ]; then echo "TRUSTED {tmp_path}/mise.toml"; exit 0; fi
if [ "$1 $2 $3" = "ls --current --json" ]; then echo '{{"python":[{{"version":"3.12.7","backend":"core:python","active":true}}]}}'; exit 0; fi
if [ "$1" = which ]; then echo "{python}"; exit 0; fi
exit 0
''')
    plan = build_environment_composition_plan(tmp_path, use_mise=True, language_provider="venv", env={"PATH": str(bin_dir)})
    assert plan.mise.version == "2026.9.1"
    assert plan.mise.trust_state == "trusted"
    assert dict(plan.mise.resolved_versions)["python"] == "3.12.7"
    assert dict(plan.mise.resolved_backends)["python"] == "core:python"


def test_poetry_receipt_captures_semantic_inputs_and_locked_consistency(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.poetry]\nname="demo"\nversion="1"\n', encoding="utf-8")
    (tmp_path / "poetry.lock").write_text("# lock\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir(); py = Path(os.sys.executable); envroot = tmp_path / ".poetry-env"; envroot.mkdir()
    _exe(bin_dir / "poetry", f'''#!/bin/sh
if [ "$1" = --version ]; then echo "Poetry (version 2.5.0)"; exit 0; fi
if [ "$1 $2 $3" = "env info --executable" ]; then echo "{py}"; exit 0; fi
if [ "$1 $2 $3" = "env info --path" ]; then echo "{envroot}"; exit 0; fi
if [ "$1 $2" = "check --lock" ]; then echo "All set"; exit 0; fi
exit 1
''')
    info = PythonProjectInfo(tmp_path, "poetry", "pytest", "", "demo", "poetry.lock")
    request = PythonEnvironmentRequest(root=str(tmp_path), manager="poetry", configured_python=str(py), configured_python_explicit=True, lock_policy="require", groups=("test",), extras=("speed",), runtime_provider="asdf")
    resolved = resolve_python_environment(info, request, env={"PATH": str(bin_dir)})
    receipt = resolved.manager_receipt
    assert receipt.manager_version == "Poetry (version 2.5.0)"
    assert receipt.groups == ("test",) and receipt.extras == ("speed",)
    assert receipt.lock_policy == "require" and receipt.runtime_provider == "asdf"
    assert receipt.lock_digest


def test_poetry_require_lock_fails_on_inconsistent_lock(tmp_path: Path) -> None:
    (tmp_path / "poetry.lock").write_text("stale\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir(); py = Path(os.sys.executable); envroot = tmp_path / ".poetry-env"; envroot.mkdir()
    _exe(bin_dir / "poetry", f'''#!/bin/sh
if [ "$1 $2 $3" = "env info --executable" ]; then echo "{py}"; exit 0; fi
if [ "$1 $2 $3" = "env info --path" ]; then echo "{envroot}"; exit 0; fi
if [ "$1 $2" = "check --lock" ]; then echo "Lock is stale"; exit 1; fi
[ "$1" = --version ] && echo "Poetry 2.5.0"
exit 0
''')
    info = PythonProjectInfo(tmp_path, "poetry", "pytest", "", "demo", "poetry.lock")
    request = PythonEnvironmentRequest(root=str(tmp_path), manager="poetry", configured_python=str(py), configured_python_explicit=True, lock_policy="require")
    with pytest.raises(PythonProjectError, match="consistent existing lock"):
        resolve_python_environment(info, request, env={"PATH": str(bin_dir)})


def test_direnv_auto_is_optional_when_executable_is_absent(tmp_path: Path) -> None:
    (tmp_path / ".envrc").write_text("export DEMO=1\n", encoding="utf-8")
    plan = build_environment_composition_plan(tmp_path, use_mise=False, language_provider="python", env={"PATH": ""})
    assert plan.direnv.present
    assert not plan.direnv.requested
    assert not plan.direnv.active
    plan.require_valid()


def test_asdf_plugin_receipt_redacts_remote_credentials(tmp_path: Path) -> None:
    from devtool.environment_composition import qualify_asdf
    (tmp_path / ".tool-versions").write_text("python 3.12.7\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    _exe(bin_dir / "asdf", '''#!/bin/sh
if [ "$1" = version ]; then echo v0.18.0; exit 0; fi
if [ "$1 $2 $3" = "plugin list --urls" ]; then echo "python https://sekret-token@github.com/acme/private-asdf-python.git?token=oops"; exit 0; fi
exit 0
''')
    receipt = qualify_asdf(tmp_path, selected=True, env={"PATH": str(bin_dir)})
    payload = json.dumps(receipt.to_dict())
    assert "sekret-token" not in payload and "token=oops" not in payload
    assert dict(receipt.plugins)["python"] == "https://github.com/acme/private-asdf-python.git"




def test_direnv_auto_installed_but_blocked_requires_action(tmp_path: Path) -> None:
    (tmp_path / ".envrc").write_text("export DEMO=1\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(
        bin_dir / "direnv",
        """#!/bin/sh
if [ \"$1 $2\" = \"status --json\" ]; then echo '{\"state\":{\"foundRC\":{\"status\":{\"text\":\"DENIED\"}}}}'; exit 0; fi
if [ \"$1\" = version ]; then echo 2.37.1; exit 0; fi
exit 0
""",
    )
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, language_provider="python", env={"PATH": str(bin_dir)}
    )
    assert plan.direnv.present and plan.direnv.requested
    assert not plan.direnv.authorized and not plan.direnv.active
    assert plan.direnv.status == "denied"
    with pytest.raises(ValueError, match="not explicitly authorized"):
        plan.require_valid()


def test_direnv_path_cannot_replace_asdf_owned_tool(tmp_path: Path) -> None:
    (tmp_path / ".envrc").write_text("export PATH=overlay\n", encoding="utf-8")
    (tmp_path / ".tool-versions").write_text("python 3.12.7\n", encoding="utf-8")
    provider = tmp_path / "provider"
    overlay = tmp_path / "overlay"
    provider.mkdir()
    overlay.mkdir()
    provider_python = _exe(provider / "python")
    _exe(overlay / "python")
    _exe(
        provider / "asdf",
        f"""#!/bin/sh
if [ \"$1\" = version ]; then echo v0.18.0; exit 0; fi
if [ \"$1\" = which ]; then echo \"{provider_python}\"; exit 0; fi
if [ \"$1\" = current ]; then echo \"python 3.12.7 {tmp_path}/.tool-versions\"; exit 0; fi
exit 0
""",
    )
    _exe(
        provider / "direnv",
        f"""#!/bin/sh
if [ \"$1 $2\" = \"status --json\" ]; then echo '{{\"state\":{{\"foundRC\":{{\"status\":{{\"text\":\"ALLOWED\"}}}}}}}}'; exit 0; fi
if [ \"$1 $2\" = \"export json\" ]; then echo '{{\"PATH\":\"{overlay}\"}}'; exit 0; fi
if [ \"$1\" = version ]; then echo 2.37.1; exit 0; fi
exit 0
""",
    )
    plan = build_environment_composition_plan(
        tmp_path,
        use_mise=False,
        use_asdf=True,
        language_provider="venv",
        env={"PATH": str(provider)},
    )
    with pytest.raises(ValueError, match="direnv PATH selects python"):
        plan.require_valid()


def test_asdf_version_override_is_provenance_bearing(tmp_path: Path) -> None:
    from devtool.environment_composition import qualify_asdf

    (tmp_path / ".tool-versions").write_text("python 3.12.7\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "asdf", '#!/bin/sh\n[ "$1" = version ] && echo v0.18.0\nexit 0\n')
    base = qualify_asdf(tmp_path, selected=True, env={"PATH": str(bin_dir)})
    overridden = qualify_asdf(
        tmp_path,
        selected=True,
        env={"PATH": str(bin_dir), "ASDF_PYTHON_VERSION": "3.13.1"},
    )
    assert dict(overridden.version_overrides)["ASDF_PYTHON_VERSION"] == "3.13.1"
    assert overridden.identity != base.identity


def test_asdf_custom_tool_versions_filename_participates_in_auto_selection_and_receipt(tmp_path: Path) -> None:
    from devtool.environment_composition import qualify_asdf
    from devtool.toolchains import selected_toolchain_manager

    (tmp_path / "versions.devtool").write_text("python 3.12.7\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(
        bin_dir / "asdf",
        f'''#!/bin/sh
if [ "$1" = version ]; then echo v0.18.0; exit 0; fi
if [ "$1" = current ]; then echo "python 3.12.7 {tmp_path}/versions.devtool"; exit 0; fi
exit 0
''',
    )
    env = {"PATH": str(bin_dir), "ASDF_TOOL_VERSIONS_FILENAME": "versions.devtool"}
    config = ProjectConfig(repo_root=tmp_path, kind="python", project_root=str(tmp_path), toolchain_manager="auto")
    assert selected_toolchain_manager(config, cwd=str(tmp_path), env=env) == "asdf"
    receipt = qualify_asdf(tmp_path, selected=True, env=env)
    assert receipt.tool_versions_filename == "versions.devtool"
    assert dict(receipt.selected_tools)["python"] == "3.12.7"
    assert dict(receipt.resolved_versions)["python"] == "3.12.7"


def test_asdf_invalid_config_path_and_unresolved_current_fail_closed(tmp_path: Path) -> None:
    (tmp_path / ".tool-versions").write_text("python 3.12.7\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "asdf", '#!/bin/sh\n[ "$1" = version ] && echo v0.18.0\nexit 0\n')
    plan = build_environment_composition_plan(
        tmp_path,
        use_mise=False,
        use_asdf=True,
        language_provider="venv",
        env={"PATH": str(bin_dir), "ASDF_CONFIG_FILE": "relative/.asdfrc"},
    )
    with pytest.raises(ValueError, match="ASDF_CONFIG_FILE must be absolute"):
        plan.require_valid()

    plan = build_environment_composition_plan(
        tmp_path,
        use_mise=False,
        use_asdf=True,
        language_provider="venv",
        env={"PATH": str(bin_dir)},
    )
    with pytest.raises(ValueError, match="could not resolve current versions for: python"):
        plan.require_valid()


def test_pyenv_explicit_provider_resolves_runtime_and_wraps(tmp_path: Path) -> None:
    from devtool.environment_composition import qualify_pyenv
    from devtool.toolchains import selected_toolchain_manager

    (tmp_path / ".python-version").write_text("3.13.2\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    python = _exe(bin_dir / "python3.13")
    pyenv = _exe(bin_dir / "pyenv", f'''#!/bin/sh
if [ "$1" = --version ]; then echo "pyenv 2.6.8"; exit 0; fi
if [ "$1" = version-name ]; then echo "3.13.2"; exit 0; fi
if [ "$1" = version ]; then echo "3.13.2 (set by {tmp_path}/.python-version)"; exit 0; fi
if [ "$1" = which ] && [ "$2" = python ]; then echo "{python}"; exit 0; fi
if [ "$1" = which ] && [ "$2" = python3 ]; then echo "{python}"; exit 0; fi
exit 1
''')
    config = ProjectConfig(repo_root=tmp_path, kind="python", project_root=str(tmp_path), toolchain_manager="pyenv")
    assert selected_toolchain_manager(config, cwd=str(tmp_path), env={"PATH": str(bin_dir)}) == "pyenv"
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, use_pyenv=True, language_provider="venv", env={"PATH": str(bin_dir)}
    )
    receipt = plan.pyenv
    assert receipt.executable.path == str(pyenv)
    assert receipt.version == "pyenv 2.6.8"
    assert receipt.requested_versions == ("3.13.2",)
    assert receipt.active_versions == ("3.13.2",)
    assert dict(receipt.resolved_tools)["python"] == str(python)
    assert receipt.version_source.endswith(".python-version")
    wrapped = apply_composition_to_spec(
        CommandSpec("test", ["python", "-V"], env={"PATH": str(bin_dir)}, cwd=str(tmp_path)), plan
    )
    assert Path(wrapped.argv[0]).name == "pyenv"
    assert wrapped.argv[1:3] == ["exec", "python"]
    assert wrapped.metadata["environment_domains"]["pyenv"] == receipt.identity
    assert wrapped.metadata["environment_domains"]["toolchain"] == receipt.identity


def test_python_version_auto_selects_pyenv_only_for_python_targets(tmp_path: Path) -> None:
    from devtool.toolchains import selected_toolchain_manager

    (tmp_path / ".python-version").write_text("3.12.7\n", encoding="utf-8")
    python_config = ProjectConfig(repo_root=tmp_path, kind="python", project_root=str(tmp_path), toolchain_manager="auto")
    go_config = ProjectConfig(repo_root=tmp_path, kind="go", project_root=str(tmp_path), toolchain_manager="auto")

    def which(name: str, path: str | None = None):
        return "/usr/bin/pyenv" if name == "pyenv" else None

    with patch("devtool.toolchains.shutil.which", side_effect=which):
        assert selected_toolchain_manager(python_config, cwd=str(tmp_path), env={"PATH": "/usr/bin"}) == "pyenv"
        assert selected_toolchain_manager(go_config, cwd=str(tmp_path), env={"PATH": "/usr/bin"}) == "none"


def test_explicit_pyenv_rejected_for_go_target(tmp_path: Path) -> None:
    from devtool.toolchains import selected_toolchain_manager

    config = ProjectConfig(repo_root=tmp_path, kind="go", project_root=str(tmp_path), toolchain_manager="pyenv")
    with pytest.raises(SystemExit, match="Python-specific"):
        selected_toolchain_manager(config, cwd=str(tmp_path), env={"PATH": "/usr/bin"})


def test_asdf_shared_marker_wins_auto_before_pyenv_marker(tmp_path: Path) -> None:
    from devtool.toolchains import selected_toolchain_manager

    (tmp_path / ".tool-versions").write_text("python 3.12.7\n", encoding="utf-8")
    (tmp_path / ".python-version").write_text("3.12.7\n", encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, kind="python", project_root=str(tmp_path), toolchain_manager="auto")

    def which(name: str, path: str | None = None):
        return f"/usr/bin/{name}" if name in {"asdf", "pyenv"} else None

    with patch("devtool.toolchains.shutil.which", side_effect=which):
        assert selected_toolchain_manager(config, cwd=str(tmp_path), env={"PATH": "/usr/bin"}) == "asdf"


def test_pyenv_provider_path_change_fails_closed(tmp_path: Path) -> None:
    (tmp_path / ".python-version").write_text("3.12.7\n", encoding="utf-8")
    a = tmp_path / "a"; b = tmp_path / "b"; a.mkdir(); b.mkdir()
    _exe(a / "pyenv", '#!/bin/sh\n[ "$1" = --version ] && echo "pyenv 2.6"\nexit 0\n')
    _exe(b / "pyenv", '#!/bin/sh\n[ "$1" = --version ] && echo "pyenv 2.6"\nexit 0\n')
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, use_pyenv=True, language_provider="venv", env={"PATH": str(a)}
    )
    with pytest.raises(ValueError, match="pyenv provider changed"):
        apply_composition_to_spec(
            CommandSpec("test", ["python", "-V"], env={"PATH": str(b)}, cwd=str(tmp_path)), plan
        )


def test_direnv_cannot_override_pyenv_version(tmp_path: Path) -> None:
    (tmp_path / ".envrc").write_text("export PYENV_VERSION=3.11\n", encoding="utf-8")
    (tmp_path / ".python-version").write_text("3.12.7\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    _exe(bin_dir / "pyenv", '#!/bin/sh\n[ "$1" = --version ] && echo "pyenv 2.6"\nexit 0\n')
    _exe(bin_dir / "direnv", '''#!/bin/sh
if [ "$1 $2" = "status --json" ]; then echo '{"state":{"foundRC":{"status":{"text":"ALLOWED"}}}}'; exit 0; fi
if [ "$1 $2" = "export json" ]; then echo '{"PYENV_VERSION":"3.11"}'; exit 0; fi
exit 0
''')
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, use_pyenv=True, language_provider="venv", env={"PATH": str(bin_dir)}
    )
    with pytest.raises(ValueError, match="PYENV_VERSION"):
        plan.require_valid()


def test_pyenv_runtime_can_feed_poetry_manager_probe(tmp_path: Path) -> None:
    (tmp_path / ".python-version").write_text("3.13.2\n", encoding="utf-8")
    (tmp_path / "poetry.lock").write_text("# lock\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    python = _exe(bin_dir / "python3.13")
    poetry_env = tmp_path / ".poetry-env"; poetry_env.mkdir()
    poetry = _exe(bin_dir / "poetry", f'''#!/bin/sh
if [ "$1" = --version ]; then echo "Poetry 2.5.0"; exit 0; fi
if [ "$1 $2 $3" = "env info --executable" ]; then echo "{python}"; exit 0; fi
if [ "$1 $2 $3" = "env info --path" ]; then echo "{poetry_env}"; exit 0; fi
exit 1
''')
    _exe(bin_dir / "pyenv", f'''#!/bin/sh
if [ "$1" = --version ]; then echo "pyenv 2.6.8"; exit 0; fi
if [ "$1" = version-name ]; then echo "3.13.2"; exit 0; fi
if [ "$1" = version ]; then echo "3.13.2 (set by {tmp_path}/.python-version)"; exit 0; fi
if [ "$1" = which ] && [ "$2" = python ]; then echo "{python}"; exit 0; fi
if [ "$1" = which ] && [ "$2" = python3 ]; then echo "{python}"; exit 0; fi
if [ "$1" = which ] && [ "$2" = poetry ]; then echo "{poetry}"; exit 0; fi
exit 1
''')
    composition = build_environment_composition_plan(
        tmp_path, use_mise=False, use_pyenv=True, language_provider="poetry", env={"PATH": str(bin_dir)}
    )
    info = PythonProjectInfo(tmp_path, "poetry", "pytest", "", "demo", "poetry.lock")
    request = PythonEnvironmentRequest(
        root=str(tmp_path), runtime_provider="pyenv", manager="poetry", configured_python=str(python), configured_python_explicit=True
    )
    resolved = resolve_python_environment(info, request, composition=composition, env={"PATH": str(bin_dir)})
    assert resolved.runtime_provider == "pyenv"
    assert resolved.manager_receipt.executable == str(poetry)
    assert resolved.python == str(python)
    assert resolved.environment_root == str(poetry_env)


def test_pyenv_version_override_changes_provider_identity(tmp_path: Path) -> None:
    from devtool.environment_composition import qualify_pyenv

    (tmp_path / ".python-version").write_text("3.12.7\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    _exe(bin_dir / "pyenv", '#!/bin/sh\n[ "$1" = --version ] && echo "pyenv 2.6"\nexit 0\n')
    base = qualify_pyenv(tmp_path, selected=True, env={"PATH": str(bin_dir)})
    override = qualify_pyenv(tmp_path, selected=True, env={"PATH": str(bin_dir), "PYENV_VERSION": "3.13.2"})
    assert override.version_override == "3.13.2"
    assert override.requested_versions == ("3.13.2",)
    assert override.identity != base.identity


def test_toolchain_doctor_reports_asdf_and_pyenv_selection_as_active(tmp_path: Path) -> None:
    from devtool.toolchains import toolchain_doctor_rows

    (tmp_path / ".tool-versions").write_text("python 3.12.7\n", encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, kind="python", project_root=str(tmp_path), toolchain_manager="auto")
    with patch("devtool.toolchains.shutil.which", side_effect=lambda name, path=None: "/usr/bin/asdf" if name == "asdf" else None):
        rows = dict(toolchain_doctor_rows(config, env={"PATH": "/usr/bin"}))
    assert rows["toolchain_selected"] == "asdf"
    assert rows["toolchain_active"] == "yes"

    (tmp_path / ".tool-versions").unlink()
    (tmp_path / ".python-version").write_text("3.13.2\n", encoding="utf-8")
    with patch("devtool.toolchains.shutil.which", side_effect=lambda name, path=None: "/usr/bin/pyenv" if name == "pyenv" else None):
        rows = dict(toolchain_doctor_rows(config, env={"PATH": "/usr/bin"}))
    assert rows["toolchain_selected"] == "pyenv"
    assert rows["toolchain_active"] == "yes"


def test_go_environment_qualifies_asdf_selected_go_before_effective_toolchain(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\ngo 1.24\n", encoding="utf-8")
    (tmp_path / ".tool-versions").write_text("golang 1.24.2\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    go = _exe(bin_dir / "go", '''#!/bin/sh
if [ "$1" = version ]; then echo "go version go1.24.2 linux/amd64"; exit 0; fi
if [ "$1 $2" = "env -json" ]; then :; fi
if [ "$1" = env ]; then echo '{"GOVERSION":"go1.24.2","GOWORK":"off","GOTOOLCHAIN":"auto"}'; exit 0; fi
exit 0
''')
    _exe(bin_dir / "asdf", f'''#!/bin/sh
if [ "$1" = version ]; then echo v0.18.0; exit 0; fi
if [ "$1" = which ] && [ "$2" = golang ]; then echo "{go}"; exit 0; fi
if [ "$1" = which ] && [ "$2" = go ]; then echo "{go}"; exit 0; fi
if [ "$1" = current ]; then echo "golang 1.24.2 {tmp_path}/.tool-versions"; exit 0; fi
exit 0
''')
    # Use a direct composition because the Go provider receipt is the contract
    # that must feed ResolvedGoEnvironment before command wrapping.
    composition = build_environment_composition_plan(
        tmp_path, use_mise=False, use_asdf=True, language_provider="go", env={"PATH": str(bin_dir)}
    )
    # asdf projects commonly name the plugin golang; make sure the actual go
    # launcher identity is still fed into Go qualification when available.
    resolved_tools = dict(composition.asdf.resolved_tools)
    if "go" not in resolved_tools and "golang" in resolved_tools:
        # This fallback is intentionally asserted below after implementation.
        pass
    request = GoEnvironmentRequest(
        root=str(tmp_path), action="test", launcher="go", toolchain_provider="asdf",
        provider_identity=composition.asdf.identity, composition_identity=composition.identity,
    )
    resolved = resolve_go_environment(request, env={"PATH": str(bin_dir)}, composition=composition)
    assert resolved.toolchain_provider == "asdf"
    assert resolved.provider_identity == composition.asdf.identity
    assert resolved.composition_identity == composition.identity
    assert Path(resolved.launcher).name == "go"
    assert resolved.effective_toolchain == "go1.24.2"


def test_no_toolchain_provider_has_empty_provider_details(tmp_path: Path) -> None:
    plan = build_environment_composition_plan(tmp_path, use_mise=False, language_provider="python", env={"PATH": ""})
    layer = next(layer for layer in plan.layers if layer.role == "toolchain-provider")
    assert layer.provider == "none"
    assert layer.details == {}
    assert plan.to_dict()["schema_version"] == 2


def test_asdf_provider_same_path_binary_mutation_fails_closed(tmp_path: Path) -> None:
    (tmp_path / ".tool-versions").write_text("python 3.12.7\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    asdf = _exe(
        bin_dir / "asdf",
        f'''#!/bin/sh
if [ "$1" = version ]; then echo v0.18.0; exit 0; fi
if [ "$1" = current ]; then echo "python 3.12.7 {tmp_path}/.tool-versions"; exit 0; fi
exit 0
''',
    )
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, use_asdf=True, language_provider="venv", env={"PATH": str(bin_dir)}
    )
    asdf.write_text(asdf.read_text(encoding="utf-8") + "# mutated\n", encoding="utf-8")
    asdf.chmod(0o755)
    with pytest.raises(ValueError, match="asdf provider changed"):
        apply_composition_to_spec(
            CommandSpec("test", ["python", "-V"], env={"PATH": str(bin_dir)}, cwd=str(tmp_path)), plan
        )


def test_provider_disappearance_after_qualification_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "mise.toml").write_text('[tools]\npython="3.12"\n', encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mise = _exe(bin_dir / "mise", '#!/bin/sh\nexit 0\n')
    plan = build_environment_composition_plan(
        tmp_path, use_mise=True, language_provider="venv", env={"PATH": str(bin_dir)}
    )
    mise.unlink()
    with pytest.raises(ValueError, match="mise provider changed"):
        apply_composition_to_spec(
            CommandSpec("test", ["python", "-V"], env={"PATH": str(bin_dir)}, cwd=str(tmp_path)), plan
        )


def test_direnv_status_global_config_identity_is_preserved_without_fake_dot_config(tmp_path: Path) -> None:
    (tmp_path / ".envrc").write_text("export DEMO=1\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(
        bin_dir / "direnv",
        '''#!/bin/sh
if [ "$1 $2" = "status --json" ]; then echo '{"state":{"foundRC":{"status":{"text":"ALLOWED"}}},"config":{"direnvrc":"/home/demo/.config/direnv/direnvrc"}}'; exit 0; fi
if [ "$1 $2" = "export json" ]; then echo '{"DEMO":"1"}'; exit 0; fi
if [ "$1" = version ]; then echo 2.37.1; exit 0; fi
exit 0
''',
    )
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, language_provider="python", env={"PATH": str(bin_dir)}
    )
    assert plan.direnv.global_config_identity
    first = plan.direnv.global_config_identity
    plan2 = build_environment_composition_plan(
        tmp_path, use_mise=False, language_provider="python", env={"PATH": str(bin_dir), "DIRENV_CONFIG": str(tmp_path / "cfg")}
    )
    assert plan2.direnv.global_config_identity != first


def test_asdf_explicit_environment_does_not_inherit_ambient_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.environment_composition import qualify_asdf

    ambient = tmp_path / "ambient"
    ambient.mkdir()
    (ambient / ".asdfrc").write_text("legacy_version_file = yes\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "asdf", '#!/bin/sh\n[ "$1" = version ] && echo v0.18.0\nexit 0\n')
    monkeypatch.setenv("HOME", str(ambient))
    receipt = qualify_asdf(tmp_path, selected=True, env={"PATH": str(bin_dir)})
    assert receipt.config_file is None
    assert not receipt.legacy_version_file
    assert not receipt.data_dir_identity


def test_pyenv_selected_runtime_without_resolved_python_fails_closed(tmp_path: Path) -> None:
    (tmp_path / ".python-version").write_text("3.13.2\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    _exe(bin_dir / "pyenv", '''#!/bin/sh
if [ "$1" = --version ]; then echo "pyenv 2.6.8"; exit 0; fi
if [ "$1" = version-name ]; then echo "3.13.2"; exit 0; fi
if [ "$1" = version ]; then echo "3.13.2"; exit 0; fi
exit 1
''')
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, use_pyenv=True, language_provider="venv", env={"PATH": str(bin_dir)}
    )
    plan.require_valid()
    with pytest.raises(ValueError, match="pyenv declares ownership of python but could not resolve its executable"):
        plan.require_runtime_tool("python")


def test_asdf_declared_go_without_resolved_executable_fails_closed(tmp_path: Path) -> None:
    (tmp_path / ".tool-versions").write_text("golang 1.24.2\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    _exe(bin_dir / "asdf", f'''#!/bin/sh
if [ "$1" = version ]; then echo v0.18.0; exit 0; fi
if [ "$1" = current ]; then echo "golang 1.24.2 {tmp_path}/.tool-versions"; exit 0; fi
exit 1
''')
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, use_asdf=True, language_provider="go", env={"PATH": str(bin_dir)}
    )
    plan.require_valid()
    with pytest.raises(ValueError, match="asdf declares ownership of go but could not resolve its executable"):
        plan.require_runtime_tool("go")


def test_asdf_unrelated_tool_does_not_claim_python_runtime(tmp_path: Path) -> None:
    (tmp_path / ".tool-versions").write_text("nodejs 24.0.0\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    node = _exe(bin_dir / "node")
    _exe(bin_dir / "asdf", f'''#!/bin/sh
if [ "$1" = version ]; then echo v0.18.0; exit 0; fi
if [ "$1" = which ] && [ "$2" = node ]; then echo "{node}"; exit 0; fi
if [ "$1" = current ]; then echo "nodejs 24.0.0 {tmp_path}/.tool-versions"; exit 0; fi
exit 0
''')
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, use_asdf=True, language_provider="venv", env={"PATH": str(bin_dir)}
    )
    plan.require_valid()
    assert plan.toolchain_provider == "asdf"
    assert plan.provider_owns_tool("python") is False
    assert plan.resolved_tool("python") == ""


def test_asdf_golang_plugin_maps_to_go_executable_identity(tmp_path: Path) -> None:
    (tmp_path / ".tool-versions").write_text("golang 1.24.2\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    go = _exe(bin_dir / "go")
    _exe(bin_dir / "asdf", f'''#!/bin/sh
if [ "$1" = version ]; then echo v0.18.0; exit 0; fi
if [ "$1" = which ] && [ "$2" = go ]; then echo "{go}"; exit 0; fi
if [ "$1" = current ]; then echo "golang 1.24.2 {tmp_path}/.tool-versions"; exit 0; fi
exit 0
''')
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, use_asdf=True, language_provider="go", env={"PATH": str(bin_dir)}
    )
    plan.require_valid()
    assert dict(plan.asdf.selected_tools)["golang"] == "1.24.2"
    assert dict(plan.asdf.resolved_tools)["go"] == str(go)
    assert plan.provider_owns_tool("go") is True


def test_asdf_current_version_first_output_is_parsed_as_version(tmp_path: Path) -> None:
    (tmp_path / ".tool-versions").write_text("python 3.12.7\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    python = _exe(bin_dir / "python")
    _exe(bin_dir / "asdf", f'''#!/bin/sh
if [ "$1" = version ]; then echo v0.18.0; exit 0; fi
if [ "$1" = which ] && [ "$2" = python ]; then echo "{python}"; exit 0; fi
if [ "$1" = current ] && [ "$2" = python ]; then echo "3.12.7 (set by {tmp_path}/.tool-versions)"; exit 0; fi
exit 0
''')
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, use_asdf=True, language_provider="venv", env={"PATH": str(bin_dir)}
    )
    plan.require_valid()
    assert dict(plan.asdf.resolved_versions)["python"] == "3.12.7"


def test_asdf_system_passthrough_does_not_claim_python_runtime(tmp_path: Path) -> None:
    (tmp_path / ".tool-versions").write_text("python system\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    system_python = _exe(bin_dir / "python")
    _exe(bin_dir / "asdf", f'''#!/bin/sh
if [ "$1" = version ]; then echo v0.18.0; exit 0; fi
if [ "$1" = which ] && [ "$2" = python ]; then echo "{system_python}"; exit 0; fi
if [ "$1" = current ] && [ "$2" = python ]; then echo "system (set by {tmp_path}/.tool-versions)"; exit 0; fi
exit 0
''')
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, use_asdf=True, language_provider="venv", env={"PATH": str(bin_dir)}
    )
    plan.require_valid()
    assert plan.toolchain_provider == "asdf"
    assert plan.provider_passes_through_tool("python") is True
    assert plan.require_runtime_tool("python") == ""


def test_pyenv_system_passthrough_does_not_claim_python_runtime(tmp_path: Path) -> None:
    (tmp_path / ".python-version").write_text("system\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    system_python = _exe(bin_dir / "python")
    _exe(bin_dir / "pyenv", f'''#!/bin/sh
if [ "$1" = --version ]; then echo "pyenv 2.6.8"; exit 0; fi
if [ "$1" = version-name ]; then echo "system"; exit 0; fi
if [ "$1" = version ]; then echo "system (set by {tmp_path}/.python-version)"; exit 0; fi
if [ "$1" = which ] && [ "$2" = python ]; then echo "{system_python}"; exit 0; fi
if [ "$1" = which ] && [ "$2" = python3 ]; then echo "{system_python}"; exit 0; fi
exit 0
''')
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, use_pyenv=True, language_provider="venv", env={"PATH": str(bin_dir)}
    )
    plan.require_valid()
    assert plan.provider_passes_through_tool("python") is True
    assert plan.require_runtime_tool("python") == ""


def test_pyenv_dir_and_legacy_version_file_are_provenance_bearing(tmp_path: Path) -> None:
    project = tmp_path / "project"; project.mkdir()
    alternate = tmp_path / "alternate"; alternate.mkdir()
    legacy = alternate / ".pyenv-version"
    legacy.write_text("3.11.9\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    python = _exe(bin_dir / "python")
    _exe(bin_dir / "pyenv", f'''#!/bin/sh
if [ "$1" = --version ]; then echo "pyenv 2.6.8"; exit 0; fi
if [ "$1" = version-name ]; then echo "3.11.9"; exit 0; fi
if [ "$1" = version ]; then echo "3.11.9 (set by {legacy})"; exit 0; fi
if [ "$1" = which ] && [ "$2" = python ]; then echo "{python}"; exit 0; fi
if [ "$1" = which ] && [ "$2" = python3 ]; then echo "{python}"; exit 0; fi
exit 0
''')
    plan = build_environment_composition_plan(
        project, use_mise=False, use_pyenv=True, language_provider="venv",
        env={"PATH": str(bin_dir), "PYENV_DIR": str(alternate)},
    )
    plan.require_valid()
    assert plan.pyenv.pyenv_dir == str(alternate.resolve())
    assert plan.pyenv.requested_versions == ("3.11.9",)
    assert plan.pyenv.version_files[0][0] == str(legacy.resolve())
    assert plan.pyenv.version_source == str(legacy)
    assert plan.pyenv.version_source_digest


def test_pyenv_requested_and_active_version_mismatch_fails_closed(tmp_path: Path) -> None:
    (tmp_path / ".python-version").write_text("3.13.2\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    python = _exe(bin_dir / "python")
    _exe(bin_dir / "pyenv", f'''#!/bin/sh
if [ "$1" = --version ]; then echo "pyenv 2.6.8"; exit 0; fi
if [ "$1" = version-name ]; then echo "3.12.7"; exit 0; fi
if [ "$1" = version ]; then echo "3.12.7 (set by somewhere-else)"; exit 0; fi
if [ "$1" = which ] && [ "$2" = python ]; then echo "{python}"; exit 0; fi
if [ "$1" = which ] && [ "$2" = python3 ]; then echo "{python}"; exit 0; fi
exit 0
''')
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, use_pyenv=True, language_provider="venv", env={"PATH": str(bin_dir)}
    )
    with pytest.raises(ValueError, match="requested versions do not match"):
        plan.require_runtime_tool("python")


def test_pyenv_auto_selection_honors_pyenv_dir_and_legacy_marker(tmp_path: Path) -> None:
    from devtool.toolchains import selected_toolchain_manager

    project = tmp_path / "project"; project.mkdir()
    alternate = tmp_path / "alternate"; alternate.mkdir()
    (alternate / ".pyenv-version").write_text("3.11.9\n", encoding="utf-8")
    config = ProjectConfig(repo_root=project, kind="python", project_root=str(project), toolchain_manager="auto")

    with patch("devtool.toolchains.shutil.which", side_effect=lambda name, path=None: "/usr/bin/pyenv" if name == "pyenv" else None):
        assert selected_toolchain_manager(
            config, cwd=str(project), env={"PATH": "/usr/bin", "PYENV_DIR": str(alternate)}
        ) == "pyenv"


def test_asdf_dir_and_data_dir_are_validated_and_provenance_bearing(tmp_path: Path) -> None:
    (tmp_path / ".tool-versions").write_text("python 3.12.7\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    python = _exe(bin_dir / "python")
    _exe(bin_dir / "asdf", f'''#!/bin/sh
if [ "$1" = version ]; then echo v0.18.0; exit 0; fi
if [ "$1" = which ] && [ "$2" = python ]; then echo "{python}"; exit 0; fi
if [ "$1" = current ] && [ "$2" = python ]; then echo "3.12.7 (set by {tmp_path}/.tool-versions)"; exit 0; fi
exit 0
''')
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, use_asdf=True, language_provider="venv",
        env={"PATH": str(bin_dir), "ASDF_DIR": "relative/core", "ASDF_DATA_DIR": "relative/data"},
    )
    with pytest.raises(ValueError, match="ASDF_DIR must be absolute"):
        plan.require_valid()

    core = tmp_path / "asdf-core"; data = tmp_path / "asdf-data"
    plan = build_environment_composition_plan(
        tmp_path, use_mise=False, use_asdf=True, language_provider="venv",
        env={"PATH": str(bin_dir), "ASDF_DIR": str(core), "ASDF_DATA_DIR": str(data)},
    )
    plan.require_valid()
    assert plan.asdf.asdf_dir == str(core.resolve())
    assert plan.asdf.data_dir == str(data.resolve())
    assert plan.asdf.data_dir_identity


def test_toolchain_wrapper_reverification_uses_authoritative_execution_path_when_spec_env_omits_path(tmp_path: Path) -> None:
    """Provider verification must not confuse CommandSpec.env with the full process environment."""
    (tmp_path / ".tool-versions").write_text("python 3.12.7\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    python = _exe(bin_dir / "python")
    _exe(bin_dir / "asdf", f'''#!/bin/sh
if [ "$1" = version ]; then echo "v0.18.0"; exit 0; fi
if [ "$1 $2 $3" = "plugin list --urls" ]; then echo "python https://github.com/asdf-community/asdf-python.git"; exit 0; fi
if [ "$1" = which ] && [ "$2" = python ]; then echo "{python}"; exit 0; fi
if [ "$1" = current ] && [ "$2" = python ]; then echo "python 3.12.7"; exit 0; fi
exit 0
''')
    config = ProjectConfig(repo_root=tmp_path, kind="python", project_root=str(tmp_path), toolchain_manager="asdf")
    spec = CommandSpec("test", ["python", "-V"], cwd=str(tmp_path))

    wrapped = apply_toolchain_wrapper(config, [spec], env={"PATH": str(bin_dir)})[0]

    assert Path(wrapped.argv[0]).name == "asdf"
    assert wrapped.argv[1:3] == ["exec", "python"]
    assert wrapped.env == {}


def test_asdf_version_override_without_version_file_establishes_runtime_ownership(tmp_path: Path) -> None:
    """ASDF_<TOOL>_VERSION is selection truth, not identity-only decoration."""
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    python = _exe(bin_dir / "python")
    _exe(bin_dir / "asdf", f'''#!/bin/sh
if [ "$1" = version ]; then echo "v0.18.0"; exit 0; fi
if [ "$1 $2 $3" = "plugin list --urls" ]; then echo "python https://github.com/asdf-community/asdf-python.git"; exit 0; fi
if [ "$1" = which ] && [ "$2" = python ]; then echo "{python}"; exit 0; fi
if [ "$1" = current ] && [ "$2" = python ]; then echo "python 3.12.7"; exit 0; fi
exit 0
''')

    plan = build_environment_composition_plan(
        tmp_path,
        use_mise=False,
        use_asdf=True,
        language_provider="venv",
        env={"PATH": str(bin_dir), "ASDF_PYTHON_VERSION": "3.12.7"},
    )

    assert dict(plan.asdf.selected_tools)["python"] == "3.12.7"
    assert dict(plan.asdf.version_overrides)["ASDF_PYTHON_VERSION"] == "3.12.7"
    assert dict(plan.asdf.resolved_versions)["python"] == "3.12.7"
    assert dict(plan.asdf.resolved_tools)["python"] == str(python)
    assert plan.provider_owns_tool("python") is True
    assert plan.require_runtime_tool("python") == str(python)
