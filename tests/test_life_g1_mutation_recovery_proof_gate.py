from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from devtool.artifact_tests import run_artifact_tests
from devtool.execution_environments import detect_current_environment
from devtool.artifacts import create_overlay_bundle, read_artifact_manifest, write_archive_entries
from devtool.transaction_worktree import transaction_execution_root
from devtool.transactions import (
    create_apply_transaction,
    mark_transaction_validated,
    integrate_transaction_worktree,
    record_transaction_git_commit,
    resumable_result_publication,
    resume_transaction_result_publication,
    rollback_transaction,
)


ROOT = Path(__file__).resolve().parents[1]

FRESH_PROCESS_NODEIDS = [
    "tests/test_life_x01_immediate_lifecycle_safety.py::test_life_x01_snapshot_direct_apply_fails_closed_before_mutation",
    "tests/test_life_x01_immediate_lifecycle_safety.py::test_life_x01_undeclared_hook_write_is_contained_even_outside_transaction_paths",
    "tests/test_life_x01_immediate_lifecycle_safety.py::test_life_x01_transaction_state_is_private_and_history_redacts_secrets",
    "tests/test_life_x01_immediate_lifecycle_safety.py::test_life_x01_public_transaction_isolation_defaults_safe",
    "tests/test_life_x01_transaction_lifecycle_recovery.py::test_life_x01_still_rejects_real_dirty_path_content_conflict",
    "tests/test_life_x01_transaction_lifecycle_recovery.py::test_life_x01_retries_preflight_failure_without_fabricating_resume_checkpoint[overlay]",
    "tests/test_life_x01_transaction_lifecycle_recovery.py::test_life_x01_partial_rollback_resumes_from_persisted_checkpoint[overlay_bundle]",
    "tests/test_life_x01_transaction_lifecycle_recovery.py::test_life_x01_verification_failure_remains_typed_and_retryable[snapshot]",
    "tests/test_life_x01_transaction_lifecycle_recovery.py::test_life_x01_transaction_reservation_never_self_blocks",
    "tests/test_life_x02_canonical_artifact_trust_pack.py::test_qualification_accumulates_blockers_and_unsafe_authorization_is_typed",
    "tests/test_life_x02_canonical_artifact_trust_pack.py::test_plan_contains_mutation_validation_expected_tree_and_leases",
    "tests/test_life_x02_canonical_artifact_trust_pack.py::test_signing_registry_is_private_non_overwriting_and_non_secret",
    "tests/test_life_x02_canonical_artifact_trust_pack.py::test_ephemeral_signing_secret_staging_is_private_and_always_cleaned",
    "tests/test_life_x02_canonical_artifact_trust_pack.py::test_pack_source_stability_rejects_changes_after_plan",
    "tests/test_life_x02_canonical_artifact_trust_pack.py::test_pack_is_deterministic_for_same_plan_and_source",
]


def _fresh_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    # A LIFE-G1 fresh-process proof must keep real host markers (for example
    # Termux PREFIX/HOME) while discarding process-local routing identity and
    # the caller's user/global Devtool policy. Otherwise the fixture can be
    # rejected before transaction recovery runs (for example by inheriting a
    # legacy DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT=host-android value).
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
        "DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT",
        "DEVTOOL_EXECUTION_ENVIRONMENT",
        "DEVTOOL_EXECUTION_CONTEXT",
        "DEVTOOL_ACTIVE_TARGET",
        "DEVTOOL_RUN_ID",
        "DEVTOOL_GLOBAL_CONFIG",
    ):
        env.pop(key, None)
    config_home = tmp_path / "fresh-config"
    config_home.mkdir(parents=True, exist_ok=True)
    env["XDG_CONFIG_HOME"] = str(config_home)
    env["DEVTOOL_GLOBAL_CONFIG"] = str(config_home / "devtool" / "config.toml")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _canonical_parent_execution_environment() -> str:
    """Bind nested recovery probes to a canonical substrate token.

    LIFE-G1 owns transaction publication/recovery semantics, not automatic host
    classification.  A validation runner may intentionally sanitize HOME/PREFIX
    markers before launching pytest, which can make a nested process describe the
    Android kernel as the non-canonical diagnostic substrate ``host-android``.
    Explicitly bind the child to the parent process' canonical execution substrate
    so this proof reaches recovery logic deterministically.
    """
    env = os.environ
    executable = str(Path(sys.executable))
    prefix = str(env.get("PREFIX") or "")
    home = str(env.get("HOME") or "")
    if (
        env.get("TERMUX_VERSION")
        or prefix.startswith("/data/data/com.termux/files/usr")
        or home.startswith("/data/data/com.termux/files/home")
        or executable.startswith("/data/data/com.termux/")
    ):
        return "native-termux"
    current = detect_current_environment()
    if current in {
        "native-termux", "chroot", "desktop-linux", "desktop-macos",
        "desktop-windows", "ssh", "proot-distro",
    }:
        return current
    return "host"


def _fresh_devtool_build_argv(repo: Path) -> list[str]:
    return [
        sys.executable, "-m", "devtool",
        "-r", str(repo), "--target", "devtool",
        "--execution-environment", _canonical_parent_execution_environment(),
        "--dry-run", "build",
    ]


def test_life_g1_fresh_process_command_never_reuses_legacy_host_android(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_EXECUTION_ENVIRONMENT", "host-android")
    argv = _fresh_devtool_build_argv(Path("/tmp/life-g1-fixture"))
    idx = argv.index("--execution-environment")
    assert argv[idx + 1] != "host-android"
    assert argv[idx + 1] in {
        "native-termux", "chroot", "desktop-linux", "desktop-macos",
        "desktop-windows", "ssh", "proot-distro", "host",
    }


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True, check=False, timeout=15
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return (proc.stdout or "").strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "LIFE G1 Fixture")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    (root / ".devtool.toml").write_text(
        "[project]\n"
        "kind = \"command\"\n"
        "build_command = \"python -c \\\"print('LIFE_G1_BUILD_OK')\\\"\"\n"
        "[workspace]\n"
        "default_target = \"devtool\"\n"
        "[targets.devtool]\n"
        "runner = \"command\"\n"
        "root = \".\"\n"
        "build_command = \"python -c \\\"print('LIFE_G1_BUILD_OK')\\\"\"\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return root


def _isolated_result_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, user_drift: bool = False
) -> tuple[Path, Path, str]:
    repo = _repo(tmp_path)
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
    monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "new")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "life-g1-publication")
    monkeypatch.setenv("DEVTOOL_ACTIVE_TARGET", "devtool")
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("tracked.txt", "validated-result\n")
    tx = create_apply_transaction(
        repo,
        artifact,
        artifact_type="overlay",
        preview={"changes": [{"kind": "modified", "path": "tracked.txt"}], "counts": {}},
    )
    worktree = transaction_execution_root(tx)
    (worktree / "tracked.txt").write_text("validated-result\n", encoding="utf-8")
    _git(worktree, "add", "tracked.txt")
    _git(worktree, "config", "user.email", "fixture@example.invalid")
    _git(worktree, "config", "user.name", "LIFE G1 Fixture")
    _git(worktree, "commit", "-qm", "validated result")
    result_commit = _git(worktree, "rev-parse", "HEAD")
    mark_transaction_validated(tx["path"], session_id="life-g1", commit_pending=True)
    record_transaction_git_commit(tx["path"], result_commit)
    if user_drift:
        (repo / "tracked.txt").write_text("user-drift\n", encoding="utf-8")
    return repo, Path(str(tx["path"])), result_commit


def test_life_g1_fresh_process_window_proof(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", *FRESH_PROCESS_NODEIDS],
        cwd=ROOT,
        env=_fresh_env(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=240,
    )
    assert proc.returncode == 0, proc.stdout
    assert "15 passed" in proc.stdout, proc.stdout


def test_life_g1_result_commit_is_not_auto_published_before_validation_seals_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg-unvalidated"))
    monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
    monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "new")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "life-g1-unvalidated-publication")
    monkeypatch.setenv("DEVTOOL_ACTIVE_TARGET", "devtool")
    artifact = tmp_path / "overlay-unvalidated.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("tracked.txt", "unvalidated-result\n")
    tx = create_apply_transaction(
        repo,
        artifact,
        artifact_type="overlay",
        preview={"changes": [{"kind": "modified", "path": "tracked.txt"}], "counts": {}},
    )
    worktree = transaction_execution_root(tx)
    (worktree / "tracked.txt").write_text("unvalidated-result\n", encoding="utf-8")
    _git(worktree, "add", "tracked.txt")
    _git(worktree, "config", "user.email", "fixture@example.invalid")
    _git(worktree, "config", "user.name", "LIFE G1 Fixture")
    _git(worktree, "commit", "-qm", "unvalidated result")
    result_commit = _git(worktree, "rev-parse", "HEAD")
    record_transaction_git_commit(tx["path"], result_commit)

    assert resumable_result_publication(tx["path"]) is None
    assert resume_transaction_result_publication(tx["path"]) is None
    assert _git(repo, "rev-parse", "HEAD") != result_commit


def test_life_g1_validated_result_commit_resumes_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, tx_path, result_commit = _isolated_result_transaction(tmp_path, monkeypatch)
    evidence = resumable_result_publication(tx_path)
    assert evidence is not None
    assert evidence["result_commit"] == result_commit
    assert evidence["isolation_state"] == "ready"

    result = resume_transaction_result_publication(tx_path)

    assert result is not None and result.returncode == 0, result.output if result else "missing result"
    assert result.metadata["publication_state"] == "completed"
    assert _git(repo, "rev-parse", "HEAD") == result_commit
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "validated-result\n"
    assert not tx_path.exists()
    assert resume_transaction_result_publication(tx_path) is None, "completed publication is idempotently closed"


def test_life_g1_fresh_process_startup_owns_and_publishes_validated_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, tx_path, result_commit = _isolated_result_transaction(tmp_path, monkeypatch)
    env = _fresh_env(tmp_path)
    env["DEVTOOL_XDG_DIR"] = str(tmp_path / "xdg")
    env["DEVTOOL_COMMAND_HEARTBEAT_SECONDS"] = "0"
    env["PYTHONPATH"] = str(ROOT)

    proc = subprocess.run(
        _fresh_devtool_build_argv(repo),
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=60,
    )

    assert "Another devtool operation conflicts" not in proc.stdout, proc.stdout
    assert _git(repo, "rev-parse", "HEAD") == result_commit, proc.stdout
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "validated-result\n"
    assert not tx_path.exists(), proc.stdout


def test_life_g1_fresh_process_publication_drift_fails_without_auto_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, tx_path, result_commit = _isolated_result_transaction(tmp_path, monkeypatch, user_drift=True)
    env = _fresh_env(tmp_path)
    env["DEVTOOL_XDG_DIR"] = str(tmp_path / "xdg")
    env["DEVTOOL_COMMAND_HEARTBEAT_SECONDS"] = "0"
    env["PYTHONPATH"] = str(ROOT)

    proc = subprocess.run(
        _fresh_devtool_build_argv(repo),
        cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, check=False, timeout=60,
    )

    assert proc.returncode != 0
    rendered = "".join(
        " " if "\u2500" <= ch <= "\u257f" else ch
        for ch in proc.stdout
    )
    rendered = " ".join(rendered.split())
    assert "Validated transaction result publication could not resume safely" in rendered, proc.stdout
    assert "Primary checkout worktree changed" in rendered, proc.stdout
    assert "transaction rollback" not in rendered.lower(), proc.stdout
    assert _git(repo, "rev-parse", "HEAD") != result_commit
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "user-drift\n"
    assert tx_path.exists(), "publication recovery must remain explicit after primary drift"


def test_life_g1_publication_resume_rejects_primary_user_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, tx_path, result_commit = _isolated_result_transaction(tmp_path, monkeypatch, user_drift=True)
    result = resume_transaction_result_publication(tx_path)

    assert result is not None and result.returncode == 1
    assert result.failure_kind == "result_publication_recovery"
    assert "Primary checkout worktree changed" in result.output
    assert _git(repo, "rev-parse", "HEAD") != result_commit
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "user-drift\n"
    assert tx_path.exists(), "failed publication remains recoverable"


def test_life_g1_same_run_validation_ref_uses_post_apply_candidate_config(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / ".devtool.toml").write_text(
        """
[project]
runner = "python"
platform = "server"
[workspace]
default_target = "devtool"
[targets.devtool]
runner = "python"
platform = "server"
root = "."
[[test]]
id = "life-g1-new-ref"
name = "Post-apply declaration"
runner = "command"
command = ["python", "-c", "print('POST_APPLY_REF_OK')"]
""".strip() + "\n",
        encoding="utf-8",
    )
    manifest = {
        "target": "devtool",
        "validation": {"required": True, "tests": [{"ref": "life-g1-new-ref"}]},
    }

    results, raw = run_artifact_tests(candidate, manifest)

    assert len(results) == 1
    assert results[0].returncode == 0
    assert results[0].metadata["test_spec_id"] == "life-g1-new-ref"
    assert "POST_APPLY_REF_OK" in raw


def test_life_g1_bundle_factory_uses_one_rollback_authority(tmp_path: Path) -> None:
    payload = b"payload\n"
    nested = tmp_path / "nested.zip"
    write_archive_entries(
        nested,
        {
            ".devtool-artifact.json": json.dumps({
                "schema_version": 2,
                "type": "overlay",
                "inventory": {"format": 1, "entries": [{
                    "path": "payload.txt", "action": "add",
                    "sha256": __import__("hashlib").sha256(payload).hexdigest(), "size": len(payload),
                }]},
            }).encode(),
            "payload.txt": payload,
        },
    )
    bundle = create_overlay_bundle(tmp_path / "bundle.zip", [nested], validation_required=True)
    validation = read_artifact_manifest(bundle)["validation"]
    assert validation["rollback_on_failure"] is True
    assert validation["failure_action"] == "rollback"


def test_life_g1_gate_ledgers_cover_both_implementation_overlays() -> None:
    x01 = json.loads((ROOT / "LIFE_X01_AUDIT_LOOP_PROMISE_LEDGER.json").read_text(encoding="utf-8"))
    x02 = json.loads((ROOT / "LIFE_X02_PROMISE_LEDGER.json").read_text(encoding="utf-8"))
    incident = json.loads((ROOT / "LIFE_PROMISE_EXTENSION_20260928.json").read_text(encoding="utf-8"))
    assert x01["campaign_position"]["full_plan"] == "31/70"
    assert x02["position"]["full_plan"] == "32/70"
    assert {f"LIFE-P{i}" for i in range(123, 128)} == set(incident["promises"])


def test_life_g1_repository_pack_preserves_leading_dotfile_names(tmp_path: Path) -> None:
    from devtool.repository_pack import RepositoryPackCompression, RepositoryPackRequest, create_repository_pack, plan_repository_pack

    repo = _repo(tmp_path / "source-dotfile")
    output = tmp_path / "dotfiles.zip"
    plan = plan_repository_pack(RepositoryPackRequest(
        repositories=(str(repo),),
        output=str(output),
        compression=RepositoryPackCompression.ZIP,
    ))
    assert any(member == ".devtool.toml" for source in plan.repositories for member in source.members)
    receipt = create_repository_pack(plan)
    assert receipt.output == str(output.resolve())
    with ZipFile(output) as archive:
        names = set(archive.namelist())
    assert any(name.endswith("/.devtool.toml") or name == ".devtool.toml" for name in names)
    assert not any(name.endswith("/devtool.toml") and not name.endswith("/.devtool.toml") for name in names)


def test_life_g1_repository_pack_failed_verification_preserves_last_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import devtool.repository_pack as pack_mod
    from devtool.repository_pack import RepositoryPackCompression, RepositoryPackRequest, create_repository_pack, plan_repository_pack

    repo = _repo(tmp_path / "source")
    output = tmp_path / "published.zip"
    output.write_bytes(b"last-known-good")
    plan = plan_repository_pack(RepositoryPackRequest(
        repositories=(str(repo),),
        output=str(output),
        compression=RepositoryPackCompression.ZIP,
    ))

    def fail_verify(*_args, **_kwargs):
        raise pack_mod.RepositoryPackError("fixture verification failure")

    monkeypatch.setattr(pack_mod, "_verify_archive", fail_verify)
    with pytest.raises(pack_mod.RepositoryPackError, match="fixture verification failure"):
        create_repository_pack(plan)

    assert output.read_bytes() == b"last-known-good"
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))



def test_life_g1_invalid_artifact_validation_runner_fails_closed(tmp_path: Path) -> None:
    candidate = _repo(tmp_path / "invalid-runner")
    manifest = {
        "target": "devtool",
        "validation": {
            "required": True,
            "tests": [{
                "id": "invalid-runner",
                "runner": "definitely-not-a-runner",
                "command": ["python", "-c", "print('MUST_NOT_RUN')"],
            }],
        },
    }

    with pytest.raises(ValueError, match="unsupported runner"):
        run_artifact_tests(candidate, manifest)


def test_life_g1_missing_rollback_evidence_is_unavailable_not_verified(tmp_path: Path) -> None:
    missing = tmp_path / "missing-transaction.json"

    result = rollback_transaction(missing, reason="LIFE-G1 missing evidence fixture")

    assert result.returncode == 2
    assert result.failure_kind == "rollback_unavailable"
    receipt = result.metadata["rollback_receipt"]
    assert receipt["state"] == "unavailable"
    assert receipt["verified"] is False
    assert receipt["scopes"] == {
        "repository": "unavailable",
        "git": "unavailable",
        "hook": "unavailable",
        "external": "unavailable",
    }


def test_life_g1_later_devtool_generation_becomes_rollback_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "generation")
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg-generation"))
    monkeypatch.setenv("DEVTOOL_TRANSACTION_ISOLATION", "worktree")
    monkeypatch.setenv("DEVTOOL_TRANSACTION_COMMIT_MODE", "new")
    artifact = tmp_path / "generation.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("tracked.txt", "payload-generation\n")
    tx = create_apply_transaction(
        repo,
        artifact,
        artifact_type="overlay",
        preview={"changes": [{"kind": "modified", "path": "tracked.txt"}], "counts": {}},
    )
    worktree = transaction_execution_root(tx)
    # Simulate payload G1 followed by a legitimate later Devtool-owned validation/
    # finalize generation before the isolated result is published.
    (worktree / "tracked.txt").write_text("payload-generation\n", encoding="utf-8")
    (worktree / "tracked.txt").write_text("later-devtool-generation\n", encoding="utf-8")
    _git(worktree, "add", "tracked.txt")
    _git(worktree, "config", "user.email", "fixture@example.invalid")
    _git(worktree, "config", "user.name", "LIFE G1 Fixture")
    _git(worktree, "commit", "-qm", "later Devtool generation")

    integrate_transaction_worktree(tx["path"])
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "later-devtool-generation\n"

    rolled_back = rollback_transaction(tx["path"], reason="LIFE-G1 generation rollback")
    assert rolled_back.returncode == 0, rolled_back.output
    assert rolled_back.metadata["rollback_receipt"]["state"] == "verified"
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "base\n"
