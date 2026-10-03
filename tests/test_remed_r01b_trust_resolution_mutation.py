from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess

import pytest

from devtool.android.instrumentation import InstrumentationError, InstrumentationRequest, instrumentation_intent, AndroidInstrumentationOrchestrator
from devtool.android.device_ops import AndroidDeviceOperations
from devtool.android.devices import AndroidDevice
from devtool.android.signing import signing_profile
from devtool.artifact_lifecycle import ArtifactEnvelopeResolver
from devtool.artifact_trust import SecretProviderRef, SigningIdentityId, SigningIdentityRecord, SigningIdentityRegistry
from devtool.artifacts import write_archive_entries
from devtool.cli import build_parser, main
from devtool.config import load_project_config
from devtool.core_registry import build_core_registry
from devtool.core_runtime import private_run_root
from devtool.core_surfaces import build_generated_surfaces
from devtool.evidence_resolver import EvidenceResolver, sha256_file
from devtool.invocation_origin import invocation_source_for_args, trusted_invocation_context
from devtool.models import CommandSpec, ProjectConfig
from devtool.project_runners import GoRunner, AndroidGradleRunner
from devtool.python_support import PythonProjectInfo, environment_fingerprint
from devtool.run_store import RunCatalog
from devtool.runners import apply_overlay, apply_overlay_bundle
from devtool.validation_sessions import ValidationSessionError, serialize_spec
from devtool.wrapper_config import WRAPPER_NATIVE_COMMANDS


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _overlay(path: Path, *, repo_name: str, trust: dict | None = None) -> Path:
    payload = b"r01\n"
    manifest: dict[str, object] = {
        "schema_version": 2,
        "type": "overlay",
        "name": "remed-r01-fixture",
        "assurance": "qualified",
        "repository": {"name": repo_name},
        "apply": {"atomic": True},
        "inventory": {"format": 1, "entries": [{"path": "r01.txt", "action": "add", "sha256": _sha(payload), "size": len(payload)}]},
        "validation": {"required": False},
    }
    if trust is not None:
        manifest["trust"] = trust
    write_archive_entries(path, {".devtool-artifact.json": json.dumps(manifest).encode(), "r01.txt": payload})
    return path


def _minimal_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".devtool.toml").write_text('[project]\nkind="python"\n', encoding="utf-8")
    return root


def _bundle(path: Path, overlays: list[Path]) -> Path:
    entries: dict[str, bytes] = {}
    rows: list[dict[str, object]] = []
    for index, overlay in enumerate(overlays, 1):
        data = overlay.read_bytes()
        arcname = f"overlays/{index:03d}_{overlay.name}"
        entries[arcname] = data
        rows.append({
            "id": f"ov{index}", "path": arcname, "required": True,
            "sha256": _sha(data), "size": len(data),
        })
    manifest = {
        "schema_version": 2, "type": "overlay_bundle", "kind": "devtool.overlay_bundle",
        "name": "r01b-bundle", "repository": {"name": "repo"},
        "overlays": rows, "apply_order": rows,
        "apply": {"atomic": True, "conflict_policy": "warn", "path_policy": "repository-relative"},
        "validation": {"required": False, "strategy": "per-overlay", "rollback_on_failure": True},
    }
    entries["devtool-bundle.json"] = json.dumps(manifest).encode()
    write_archive_entries(path, entries)
    return path


def test_r01_core_registry_is_authority_and_completion_omits_aliases() -> None:
    parser = build_parser()
    registry = build_core_registry()
    parity = registry.parser_parity(parser)
    assert parity["missing"] == []
    assert parity["extra"] == []
    assert parity["missing_options"] == []
    assert parity["extra_options"] == []
    surfaces = build_generated_surfaces(parser)
    all_completion = "\n".join(surfaces.completions.values())
    assert "apply-overlay" not in all_completion
    assert "apply-snapshot" not in all_completion


def test_r01_internal_provenance_is_not_user_argv() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--internal-invocation-source", "wrapper", "status"])
    args = parser.parse_args(["status"])
    assert invocation_source_for_args(args) == "cli"
    with trusted_invocation_context(source="wrapper", command="status", wrapper_schema="1"):
        assert invocation_source_for_args(args) == "wrapper"
    assert invocation_source_for_args(args) == "cli"


def test_r01_repository_config_rejects_unknown_and_allows_extensions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _minimal_repo(tmp_path / "repo")
    (repo / ".devtool.toml").write_text('[project]\nkind="python"\n[totally_unknown]\nfoo=1\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="unknown top-level"):
        load_project_config(str(repo), None)
    # Exercise the real CLI ingress as well: unknown repository policy must not
    # be bypassed by status/read-only command handling.
    assert main(["--repo", str(repo), "--json", "status"]) == 1
    failed = json.loads(capsys.readouterr().out)
    assert not failed["ok"]
    assert "unknown top-level" in failed["stderr"]
    (repo / ".devtool.toml").write_text('[project]\nkind="python"\n[extensions.demo]\nfoo=1\n', encoding="utf-8")
    assert load_project_config(str(repo), None).kind == "python"
    assert main(["--repo", str(repo), "--json", "status"]) == 0
    ok = json.loads(capsys.readouterr().out)
    assert ok["ok"]


def test_r01_wrapper_native_commands_are_registry_derived() -> None:
    expected = {
        cmd.canonical_path[0]
        for cmd in build_core_registry().commands
        if len(cmd.canonical_path) == 1 and bool(cmd.metadata.get("wrapper_native"))
    }
    assert set(WRAPPER_NATIVE_COMMANDS) == expected


def test_r01_identical_real_cli_operations_get_distinct_runs_same_operation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _minimal_repo(tmp_path / "repo")
    assert main(["--repo", str(repo), "--json", "status"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["ok"]
    assert main(["--repo", str(repo), "--json", "status"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["ok"]
    first_run = first["machine"]["run_id"]
    second_run = second["machine"]["run_id"]
    assert first_run and second_run and first_run != second_run
    catalog = RunCatalog.open(private_run_root(repo), create=False)
    assert first_run in catalog.runs and second_run in catalog.runs
    assert catalog.runs[first_run]["operation_id"] == catalog.runs[second_run]["operation_id"]


def test_r01_validation_state_never_persists_raw_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "TOPSECRET")
    payload = serialize_spec(CommandSpec("safe", ["tool", "--token", "@env:API_TOKEN"], env={"API_TOKEN": "TOPSECRET"}))
    text = json.dumps(payload)
    assert "TOPSECRET" not in text
    assert "@env:API_TOKEN" in text
    with pytest.raises(ValidationSessionError):
        serialize_spec(CommandSpec("unsafe", ["tool", "--token", "TOPSECRET"]))


def test_r01_nested_requirements_change_python_environment_identity(tmp_path: Path) -> None:
    root = tmp_path / "py"
    (root / "req").mkdir(parents=True)
    (root / "requirements.txt").write_text("-r req/base.txt\n", encoding="utf-8")
    (root / "req/base.txt").write_text("demo==1\n", encoding="utf-8")
    info = PythonProjectInfo(
        root=root, manager="venv", framework="", build_backend="", package_name="", lock_file="",
        dependency_files=("requirements.txt",),
    )
    first, inputs = environment_fingerprint(info, python=os.sys.executable, probe_runtime=False)
    assert "req/base.txt" in inputs
    (root / "req/base.txt").write_text("demo==2\n", encoding="utf-8")
    second, _ = environment_fingerprint(info, python=os.sys.executable, probe_runtime=False)
    assert first != second


def test_r01_go_offline_policy_reaches_real_command_environment(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.invalid/r01\n\ngo 1.23\n\nrequire example.invalid/not-cached v0.0.1\n", encoding="utf-8")
    config = ProjectConfig(
        repo_root=tmp_path, kind="go", runner="go", project_root=str(tmp_path),
        runner_options={"go": {"offline": True}},
    )
    spec = GoRunner().build_specs(config, "restore")[0]
    assert spec.env["GOPROXY"] == "off"
    assert spec.env["GOSUMDB"] == "off"
    assert spec.env["GOTOOLCHAIN"] == "local"
    assert spec.env["GOAUTH"] == "off"
    env = dict(os.environ)
    env.update(spec.env)
    result = subprocess.run(spec.argv, cwd=spec.cwd, env=env, text=True, capture_output=True, check=False, timeout=30)
    assert result.returncode != 0
    assert "example.invalid/not-cached" in (result.stdout + result.stderr)


def test_r01_repository_cannot_self_authorize_direnv(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path / "repo")
    (repo / ".devtool.toml").write_text(
        '[project]\nkind="python"\n[env]\nDEVTOOL_DIRENV_AUTHORIZED="1"\n', encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="DEVTOOL_DIRENV_AUTHORIZED"):
        load_project_config(str(repo), None)


def test_r01_sensitive_android_runner_binding_is_private(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(InstrumentationError):
        instrumentation_intent(InstrumentationRequest(project=tmp_path, runner_args={"apiToken": "TOPSECRET"}))
    monkeypatch.setenv("ANDROID_API_TOKEN", "TOPSECRET")
    intent = instrumentation_intent(InstrumentationRequest(project=tmp_path, runner_args={"apiToken": "@env:ANDROID_API_TOKEN"}))
    command = AndroidInstrumentationOrchestrator._command_for_intent(intent, "demo/Runner")
    bindings = AndroidInstrumentationOrchestrator._private_runner_bindings(intent)
    assert "TOPSECRET" not in json.dumps(intent.as_dict())
    assert "TOPSECRET" not in " ".join(command)
    assert "@env:ANDROID_API_TOKEN" not in " ".join(command)
    assert bindings == [("apiToken", "@env:ANDROID_API_TOKEN", "TOPSECRET")]


def test_r01_sensitive_android_runner_config_fails_before_child_argv(tmp_path: Path) -> None:
    (tmp_path / "settings.gradle").write_text("rootProject.name='r01b'\n", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("android { compileSdk 36 }\n", encoding="utf-8")
    config = ProjectConfig(
        repo_root=tmp_path, project_root=str(tmp_path), kind="android", runner="android", variant="debug",
        runner_options={
            "android": {"instrumentation": {"policy": "required", "runner_args": {"apiToken": "TOPSECRET"}}},
            "gradle": {"command": "gradle"},
        },
    )
    with pytest.raises(SystemExit, match="private .*binding reference"):
        AndroidGradleRunner().build_specs(config, "instrumentation")


def test_r01_private_android_transport_redacts_echoed_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Manager:
        def select(self, *_args, **_kwargs):
            return [AndroidDevice(serial="serial-1", state="device")]
        def command_prefix(self, _serial: str):
            return ["adb", "-s", "serial-1"]

    captured: dict[str, object] = {}
    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(argv, 1, stdout="runner echoed TOPSECRET\n", stderr="bad TOPSECRET\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ops = AndroidDeviceOperations(_Manager())  # type: ignore[arg-type]
    result = ops.shell_with_private_bindings(
        ["am", "instrument", "demo/Runner"], [("apiToken", "@env:ANDROID_API_TOKEN", "TOPSECRET")],
        check=False,
    )
    assert "TOPSECRET" not in " ".join(captured["argv"])
    assert captured["input"] == "TOPSECRET\n"
    assert "TOPSECRET" not in result.stdout
    assert "TOPSECRET" not in result.stderr
    assert "TOPSECRET" not in result.detail
    assert "TOPSECRET" not in json.dumps(result.metadata)
    assert "<redacted>" in result.stdout and "<redacted>" in result.stderr


def test_r01_supplied_resolved_artifact_plan_is_execution_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _overlay(tmp_path / "overlay.zip", repo_name=repo.name)
    plan = ArtifactEnvelopeResolver().resolve_qualified_plan(artifact, repo_root=repo, public_runner=False)
    def explode(*_args, **_kwargs):
        raise AssertionError("runner attempted to re-resolve/reparse canonical artifact plan")
    monkeypatch.setattr(ArtifactEnvelopeResolver, "resolve_qualified_plan", explode)
    import devtool.runners as runners_module
    monkeypatch.setattr(runners_module, "read_artifact_manifest", explode)
    result = apply_overlay(repo, str(artifact), yes=True, resolved_plan=plan)
    assert result.returncode == 0, result.output
    assert (repo / "r01.txt").read_bytes() == b"r01\n"
    assert not hasattr(runners_module, "read_delete_manifest")
    assert not hasattr(runners_module, "_apply_delete_manifest")
    assert not hasattr(runners_module, "_direct_artifact_validation_failure")


def test_r01_resolved_plan_rejects_artifact_replacement_before_mutation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _overlay(tmp_path / "overlay.zip", repo_name=repo.name)
    plan = ArtifactEnvelopeResolver().resolve_qualified_plan(artifact, repo_root=repo, public_runner=False)
    # Replace the transport bytes at the same path after qualification.  A
    # pathname match alone must never authorize the stale plan.
    _overlay(artifact, repo_name=repo.name, trust={"required": False})
    with artifact.open("ab") as handle:
        handle.write(b"replaced-after-plan")
    with pytest.raises(RuntimeError, match="transport identity mismatch"):
        apply_overlay(repo, str(artifact), yes=True, resolved_plan=plan)
    assert not (repo / "r01.txt").exists()


def test_r01_bundle_runner_does_not_reparse_nested_semantics_after_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    first = _overlay(tmp_path / "first.zip", repo_name=repo.name)
    second_payload = b"r02\n"
    second_manifest = {
        "schema_version": 2, "type": "overlay", "name": "second", "assurance": "qualified",
        "repository": {"name": repo.name}, "apply": {"atomic": True},
        "inventory": {"format": 1, "entries": [{
            "path": "r01.txt", "action": "modify", "source_sha256": _sha(b"r01\n"),
            "sha256": _sha(second_payload), "size": len(second_payload),
        }]},
        "validation": {"required": False},
    }
    second = tmp_path / "second.zip"
    write_archive_entries(second, {".devtool-artifact.json": json.dumps(second_manifest).encode(), "r01.txt": second_payload})
    bundle = _bundle(tmp_path / "bundle.zip", [first, second])
    plan = ArtifactEnvelopeResolver().resolve_qualified_plan(bundle, repo_root=repo, public_runner=False)
    assert len(plan.bundle_entries) == 2

    import devtool.runners as runners_module
    def explode(*_args, **_kwargs):
        raise AssertionError("bundle runner reparsed artifact semantics after canonical planning")
    monkeypatch.setattr(runners_module, "read_artifact_manifest", explode)
    # The old bundle materializer is no longer imported by the runner at all;
    # its absence proves it cannot silently regain mutation authority.
    assert not hasattr(runners_module, "materialize_overlay_bundle_entries")
    result = apply_overlay_bundle(repo, str(bundle), yes=True, resolved_plan=plan)
    assert result.returncode == 0, result.output
    assert (repo / "r01.txt").read_bytes() == second_payload


def test_r01_required_artifact_trust_rejects_unknown_or_fake_signature(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    registry_path = tmp_path / "identities.json"
    monkeypatch.setenv("DEVTOOL_SIGNING_IDENTITY_REGISTRY", str(registry_path))
    unknown = _overlay(tmp_path / "unknown.zip", repo_name=repo.name, trust={"required": True, "identity_id": "missing"})
    receipt = ArtifactEnvelopeResolver().qualify(ArtifactEnvelopeResolver().resolve(unknown), repo_root=repo, public_runner=False)
    assert not receipt.qualified
    assert any(item.code == "trust-verification-failed" for item in receipt.blockers)

    key = tmp_path / "verify.key"
    key.write_bytes(b"secret-key")
    SigningIdentityRegistry(registry_path).register(SigningIdentityRecord(
        identity_id=SigningIdentityId("delivery"), provider="hmac-sha256", key_reference="unused", alias="", certificate_sha256="",
        secret_provider=SecretProviderRef("private-file", str(key)), purposes=("artifact-delivery",),
    ))
    artifact = _overlay(tmp_path / "fake.zip", repo_name=repo.name, trust={"required": True, "identity_id": "delivery"})
    artifact.with_name(artifact.name + ".sig").write_text("0" * 64, encoding="utf-8")
    receipt = ArtifactEnvelopeResolver().qualify(ArtifactEnvelopeResolver().resolve(artifact), repo_root=repo, public_runner=False)
    assert not receipt.qualified
    assert any(item.code == "trust-verification-failed" for item in receipt.blockers)


def test_r01_host_trust_policy_cannot_be_weakened_by_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    global_config = tmp_path / "global.toml"
    global_config.write_text(
        "[trust.artifacts]\nrequire_signature = true\nrequire_known_identity = true\nminimum_assurance = \"qualified\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEVTOOL_GLOBAL_CONFIG", str(global_config))
    artifact = _overlay(
        tmp_path / "unsigned.zip", repo_name=repo.name,
        trust={"required": False, "require_signature": False, "require_known_identity": False, "minimum_assurance": "legacy"},
    )
    receipt = ArtifactEnvelopeResolver().qualify(ArtifactEnvelopeResolver().resolve(artifact), repo_root=repo, public_runner=False)
    assert not receipt.qualified
    codes = {item.code for item in receipt.blockers}
    assert "trust-verification-failed" in codes


def test_r01_valid_artifact_signature_qualifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    registry_path = tmp_path / "identities.json"
    key = tmp_path / "verify.key"
    key.write_bytes(b"secret-key")
    monkeypatch.setenv("DEVTOOL_SIGNING_IDENTITY_REGISTRY", str(registry_path))
    SigningIdentityRegistry(registry_path).register(SigningIdentityRecord(
        identity_id=SigningIdentityId("delivery"), provider="hmac-sha256", key_reference="unused", alias="", certificate_sha256="",
        secret_provider=SecretProviderRef("private-file", str(key)), purposes=("artifact-delivery",),
    ))
    artifact = _overlay(tmp_path / "signed.zip", repo_name=repo.name, trust={"required": True, "identity_id": "delivery"})
    signature = hmac.new(key.read_bytes(), artifact.read_bytes(), hashlib.sha256).hexdigest()
    artifact.with_name(artifact.name + ".sig").write_text(signature, encoding="utf-8")
    receipt = ArtifactEnvelopeResolver().qualify(ArtifactEnvelopeResolver().resolve(artifact), repo_root=repo, public_runner=False)
    assert receipt.qualified and receipt.trust_ready and receipt.trust_evidence_ids


def test_r01_android_signing_uses_life_identity_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry_path = tmp_path / "identities.json"
    monkeypatch.setenv("DEVTOOL_SIGNING_IDENTITY_REGISTRY", str(registry_path))
    SigningIdentityRegistry(registry_path).register(SigningIdentityRecord(
        identity_id=SigningIdentityId("android-release"), provider="android-pkcs12",
        key_reference="/life/release.p12", alias="life-alias", certificate_sha256="a" * 64,
        purposes=("android-signing",), variants=("release",), secret_provider=SecretProviderRef("local", "STORE_PASS"),
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


def test_r01_artifact_apply_persists_run_linked_execution_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _overlay(tmp_path / "overlay.zip", repo_name=repo.name)
    # A direct/library caller cannot self-authorize a canonical RUN identity by
    # setting ambient DEVTOOL_RUN_ID. The artifact runtime must allocate an
    # independent durable run unless RUN-V3 already proves an active parent.
    monkeypatch.setenv("DEVTOOL_RUN_ID", "stale-unregistered-run")
    result = apply_overlay(repo, str(artifact), yes=True)
    assert result.returncode == 0, result.output
    run_id = result.metadata["run_id"]
    assert run_id and run_id != "stale-unregistered-run"
    record_id = result.metadata["artifact_execution_record_id"]
    attachment_id = result.metadata["artifact_execution_attachment_id"]
    record_path = private_run_root(repo) / run_id / "artifacts" / (record_id.replace(":", "-") + ".json")
    assert record_path.is_file()
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == run_id
    assert payload["plan_id"] == result.metadata["resolved_artifact_plan_id"]
    catalog = RunCatalog.open(private_run_root(repo), create=False)
    assert run_id in catalog.runs
    assert attachment_id in catalog.attachments
    assert catalog.attachments[attachment_id].run_id == run_id


def test_r01_evidence_resolver_rejects_missing_and_stale_and_resolves_pytest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    test_file = repo / "tests/test_ok.py"
    test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    resolver = EvidenceResolver(repo)
    assert not resolver.resolve("does-not-exist::fake").resolved
    digest = sha256_file(test_file)
    assert resolver.resolve("tests/test_ok.py::test_ok", expected_sha256=digest).resolved
    assert not resolver.resolve("tests/test_ok.py::test_ok", expected_sha256="0" * 64, execute_pytest=False).resolved
