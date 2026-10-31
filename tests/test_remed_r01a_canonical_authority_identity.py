from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool.android.signing import signing_profile
from devtool.artifact_trust import SecretProviderRef, SigningIdentityId, SigningIdentityRecord, SigningIdentityRegistry
from devtool.cli import build_parser, main
from devtool.config import load_global_config, load_project_config
from devtool.core_parser import parser_identity_projection
from devtool.core_registry import build_core_registry
from devtool.core_runtime import ResolutionContext, build_run_envelope, private_run_root
from devtool.core_surfaces import build_generated_surfaces, check_generated_surfaces
from devtool.invocation_origin import create_managed_run_handoff, current_trusted_invocation, invocation_source_for_args, trusted_invocation_context
from devtool.models import ProjectConfig
from devtool.python_support import PythonProjectInfo, environment_fingerprint
from devtool.run_store import RunCatalog
from devtool.wrapper_config import WRAPPER_NATIVE_COMMANDS, load_wrapper_config
from devtool.wrapper_execution import execute_wrapper_resolution
from devtool.wrapper_resolution import resolve_wrapper_argv


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".devtool.toml").write_text('[project]\nkind="python"\n', encoding="utf-8")
    return root


def test_r01a_schema_compiles_parser_and_identity_rows_are_bound() -> None:
    parser = build_parser()
    registry = build_core_registry()
    parity = registry.parser_parity(parser)
    assert parity["missing_count"] == parity["extra_count"] == 0
    assert parity["option_missing_count"] == parity["option_extra_count"] == 0
    projected = parser_identity_projection()
    assert projected["commands"]
    assert projected["options"]
    source = inspect.getsource(__import__("devtool.cli", fromlist=["build_parser"]).build_parser)
    assert "build_canonical_parser" in source
    assert "add_parser" not in source and "add_argument" not in source


def test_r01a_checked_in_generated_surfaces_are_fixed_point() -> None:
    root = Path(__file__).resolve().parents[1]
    bundle = build_generated_surfaces(build_parser())
    result = check_generated_surfaces(bundle, root / "share" / "core-generated")
    assert result["status"] == "pass", result
    completion = "\n".join(bundle.completions.values())
    assert "apply-overlay" not in completion
    assert "apply" in completion


def test_r01a_user_argv_cannot_forge_internal_origin_and_wrapper_uses_private_context(tmp_path: Path) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--internal-invocation-source", "wrapper", "status"])
    args = parser.parse_args(["status"])
    assert invocation_source_for_args(args) == "cli"
    with trusted_invocation_context(source="wrapper", command="status", wrapper_schema="2"):
        assert invocation_source_for_args(args) == "wrapper"
    assert invocation_source_for_args(args) == "cli"

    repo = _repo(tmp_path / "wrapper")
    (repo / ".devtool.toml").write_text(
        '[project]\nkind="python"\n[wrapper]\nschema=2\n[wrapper.commands.build]\ndevtool="build"\n',
        encoding="utf-8",
    )
    project = load_project_config(str(repo), None, allow_ambiguous_target=True)
    resolution = resolve_wrapper_argv(project, ["build"], launch_cwd=repo)
    seen: list[tuple[list[str], object]] = []
    execute_wrapper_resolution(
        resolution,
        reenter=lambda argv: seen.append((list(argv), current_trusted_invocation())) or 0,
    )
    argv, origin = seen[0]
    assert not any(x.startswith("--internal-invocation-") or x == "--internal-wrapper-schema" for x in argv)
    assert origin is not None and origin.source == "wrapper" and origin.command == "build"


def test_r01a_repository_and_global_config_cross_one_namespace_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / ".devtool.toml").write_text('[project]\nkind="python"\n[unknown_private]\nx=1\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="unknown top-level"):
        load_project_config(str(repo), None)
    (repo / ".devtool.toml").write_text('[project]\nkind="python"\n[extensions.demo]\nx=1\n', encoding="utf-8")
    assert load_project_config(str(repo), None).kind == "python"

    global_cfg = tmp_path / "global.toml"
    global_cfg.write_text('[totally_unknown]\nx=1\n', encoding="utf-8")
    monkeypatch.setenv("DEVTOOL_GLOBAL_CONFIG", str(global_cfg))
    with pytest.raises(SystemExit, match="unknown top-level"):
        load_global_config()
    global_cfg.write_text('[x-demo]\nx=1\n', encoding="utf-8")
    loaded, _ = load_global_config()
    assert loaded["x-demo"]["x"] == 1


def test_r01a_wrapper_native_command_set_is_registry_projection() -> None:
    expected = {
        cmd.canonical_path[0]
        for cmd in build_core_registry().commands
        if len(cmd.canonical_path) == 1 and bool(cmd.metadata.get("wrapper_native"))
    }
    assert set(WRAPPER_NATIVE_COMMANDS) == expected | {"actions"}


def test_r01a_root_cli_ignores_stale_run_env_and_persists_distinct_run_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path / "repo")
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DEVTOOL_RUN_ID", "stale-parent-value")
    # Public/ambient child-role hints must not turn a stale run ID into authority.
    monkeypatch.setenv("DEVTOOL_TEXTUAL_EXECUTION_CHILD", "1")
    monkeypatch.setenv("DEVTOOL_TEXTUAL_APPLY_CHILD", "1")
    monkeypatch.setenv("DEVTOOL_FEDERATION_PARENT_RUN_ID", "forged-parent")

    assert main(["--repo", str(repo), "--json", "status"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(["--repo", str(repo), "--json", "status"]) == 0
    second = json.loads(capsys.readouterr().out)
    first_id = first["machine"]["run_id"]
    second_id = second["machine"]["run_id"]
    assert first_id and second_id and first_id != second_id
    assert first_id != "stale-parent-value" and second_id != "stale-parent-value"
    catalog = RunCatalog.open(private_run_root(repo), create=False)
    assert catalog.runs[first_id]["status"] == "succeeded"
    assert catalog.runs[second_id]["status"] == "succeeded"
    assert catalog.runs[first_id]["operation_id"] == catalog.runs[second_id]["operation_id"]


def test_r01a_managed_child_run_id_requires_one_shot_parent_handoff(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "managed-repo")
    state = tmp_path / "managed-state"
    expected = "managed-child-run-01"
    env = dict(os.environ)
    env["DEVTOOL_XDG_DIR"] = str(state)
    env["DEVTOOL_TEXTUAL_EXECUTION_CHILD"] = "1"
    env["DEVTOOL_RUN_ID"] = expected
    handoff = create_managed_run_handoff(expected)
    env["DEVTOOL_MANAGED_RUN_HANDOFF"] = handoff
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, "-m", "devtool", "--repo", str(repo), "--json", "status"]

    first = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    first_payload = json.loads(first.stdout)
    assert first_payload["machine"]["run_id"] == expected
    assert not Path(handoff).exists()

    # Reusing the same public child marker and stale DEVTOOL_RUN_ID without the
    # one-shot capability must allocate a fresh identity.
    env.pop("DEVTOOL_MANAGED_RUN_HANDOFF", None)
    second = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, check=False)
    assert second.returncode == 0, second.stderr
    second_payload = json.loads(second.stdout)
    assert second_payload["machine"]["run_id"] != expected
    assert second_payload["machine"]["run_id"] != first_payload["machine"]["run_id"]


def test_r01a_semantic_operation_id_is_stable_while_run_id_is_unique(tmp_path: Path) -> None:
    context = ResolutionContext.from_argv(["status"], repo_root=tmp_path)
    first = build_run_envelope(context)
    second = build_run_envelope(context)
    assert first.descriptor.run_id != second.descriptor.run_id
    assert first.descriptor.operation_id == second.descriptor.operation_id


def test_r01a_python_environment_identity_recurses_includes(tmp_path: Path) -> None:
    root = tmp_path / "py"
    (root / "req").mkdir(parents=True)
    (root / "requirements.txt").write_text("-r req/base.txt\n", encoding="utf-8")
    (root / "req" / "base.txt").write_text("-c constraints.txt\ndemo==1\n", encoding="utf-8")
    (root / "req" / "constraints.txt").write_text("leaf==1\n", encoding="utf-8")
    info = PythonProjectInfo(
        root=root, manager="venv", framework="", build_backend="", package_name="", lock_file="",
        dependency_files=("requirements.txt",),
    )
    first, inputs = environment_fingerprint(info, python=os.sys.executable, probe_runtime=False)
    assert "req/base.txt" in inputs and "req/constraints.txt" in inputs
    (root / "req" / "constraints.txt").write_text("leaf==2\n", encoding="utf-8")
    second, _ = environment_fingerprint(info, python=os.sys.executable, probe_runtime=False)
    assert first != second


def test_r01a_android_signing_consumes_life_identity_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry_path = tmp_path / "identities.json"
    monkeypatch.setenv("DEVTOOL_SIGNING_IDENTITY_REGISTRY", str(registry_path))
    SigningIdentityRegistry(registry_path).register(SigningIdentityRecord(
        identity_id=SigningIdentityId("android-release"),
        provider="android-pkcs12",
        key_reference="/life/release.p12",
        alias="life-alias",
        certificate_sha256="a" * 64,
        purposes=("android-signing",),
        variants=("release",),
        secret_provider=SecretProviderRef("local", "STORE_PASS"),
    ))
    config = ProjectConfig(
        repo_root=tmp_path, kind="android", runner="android", project_root=str(tmp_path), variant="release",
        runner_options={"android": {"signing": {"release": {
            "identity_id": "android-release", "mode": "devtool", "keystore": "/wrong.p12", "alias": "wrong"
        }}}},
    )
    profile = signing_profile(config, "release")
    assert profile.identity_id == "android-release"
    assert profile.keystore == "/life/release.p12"
    assert profile.alias == "life-alias"
    assert profile.store_password_env == "STORE_PASS"

    bad = ProjectConfig(
        repo_root=tmp_path, kind="android", runner="android", project_root=str(tmp_path), variant="release",
        runner_options={"android": {"signing": {"release": {"identity_id": "missing", "mode": "devtool"}}}},
    )
    with pytest.raises(Exception):
        signing_profile(bad, "release")


def test_r01a_production_config_consumers_do_not_reparse_devtool_toml() -> None:
    root = Path(__file__).resolve().parents[1] / "devtool"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name in {"config.py", "final_seal.py", "adoption_batch.py"}:
            # Adoption migration compares untrusted pre/post TOML without using
            # that parse as authority to execute a project command.
            continue
        if path.relative_to(root) == Path("android/_standalone.py"):
            # Wave D's exported Android builder runs in isolated images with no
            # Devtool config package installed. This is a bounded, deny-only
            # security gate: it can prohibit native Gradle, but cannot define
            # target identity, task selection, providers or build arguments.
            # Check that it executes *before* entering the chroot boundary.
            assert "def hosted_gradle_only(project_root:" in text
            assert 'mode == "github-actions-only"' in text
            assert "LOCAL_GRADLE_FORBIDDEN" in text
            main_start = text.index("def main(argv:")
            main_code = text[main_start:]
            assert main_code.index("enforce_hosted_gradle_only(project_root") < main_code.index("maybe_reexec_in_chroot(raw_args)")
            continue
        if '".devtool.toml"' in text and "tomllib.loads" in text:
            offenders.append(str(path.relative_to(root.parent)))
    assert offenders == [], offenders
