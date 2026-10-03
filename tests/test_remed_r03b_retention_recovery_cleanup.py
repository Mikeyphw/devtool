from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from devtool.cleanup_lifecycle import (
    CleanupScope,
    GeneratedOutputDescriptor,
    build_cleanup_ownership_view,
    compile_cleanup_plan,
)
from devtool.repository_cleanup import archive_backup_candidates, discover_backup_candidates
from devtool.repository_pack import (
    RepositoryPackCompression,
    RepositoryPackMode,
    RepositoryPackRequest,
    create_repository_pack,
    plan_repository_pack,
)
from devtool.repository_pack_registry import (
    list_repository_packs,
    prune_repository_packs,
    repository_pack_status,
    set_repository_pack_pin,
)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "devtool-test@example.invalid")
    _git(repo, "config", "user.name", "Devtool Test")
    return repo


def _commit(repo: Path, text: str) -> str:
    (repo / "payload.txt").write_text(text, encoding="utf-8")
    _git(repo, "add", "payload.txt")
    _git(repo, "commit", "-q", "-m", text.strip())
    return _git(repo, "rev-parse", "HEAD")


def _unpack_tar_zst(pack: Path, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    raw_tar = target / "pack.tar"
    with raw_tar.open("wb") as stream:
        proc = subprocess.run(
            ["zstd", "-q", "-d", "-c", str(pack)],
            stdout=stream,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    with tarfile.open(raw_tar, "r:") as archive:
        archive.extractall(target / "contents")
    return target / "contents"


def test_historical_commit_tar_zst_pack_and_bundle_are_bound_to_selected_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    old_commit = _commit(repo, "old\n")
    _commit(repo, "new\n")
    pack = tmp_path / "historical.tar.zst"
    plan = plan_repository_pack(RepositoryPackRequest(
        repositories=(str(repo),),
        output=str(pack),
        mode=RepositoryPackMode.COMMIT,
        compression=RepositoryPackCompression.TAR_ZST,
        include_git_bundle=True,
        commit=old_commit,
    ))
    assert plan.repositories[0].selected_commit == old_commit
    # Change HEAD after planning. Historical packs must remain bound to the
    # immutable selected commit rather than reverse-adopting current HEAD.
    _commit(repo, "newer-after-plan\n")

    receipt = create_repository_pack(plan)
    assert receipt.verified and receipt.published
    assert hashlib.sha256(pack.read_bytes()).hexdigest() == receipt.sha256

    unpacked = _unpack_tar_zst(pack, tmp_path / "unpacked")
    manifest = json.loads((unpacked / "devtool-repository-pack.json").read_text(encoding="utf-8"))
    repo_manifest = manifest["repositories"][0]
    assert repo_manifest["selected_commit"] == old_commit
    root = unpacked / "repositories" / repo_manifest["identity"]["fingerprint"][:20]
    assert (root / "payload.txt").read_text(encoding="utf-8") == "old\n"

    bundle = unpacked / "git" / f"{repo_manifest['identity']['fingerprint'][:20]}.bundle"
    heads = subprocess.run(
        ["git", "bundle", "list-heads", str(bundle)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert heads.returncode == 0, heads.stderr
    assert old_commit in heads.stdout
    assert _git(repo, "for-each-ref", "--format=%(refname)", "refs/devtool/repository-pack") == ""

    status = repository_pack_status(repo, receipt.pack_id)
    assert status["verified"] is True
    assert status["selected_commit"] == old_commit


def test_repository_pack_pin_blocks_real_gc_then_unpin_allows_delete(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "one\n")
    pack = tmp_path / "retained.tar.gz"
    plan = plan_repository_pack(RepositoryPackRequest(
        repositories=(str(repo),), output=str(pack), mode=RepositoryPackMode.TRACKED,
        compression=RepositoryPackCompression.TAR_GZ, pin=True,
    ))
    receipt = create_repository_pack(plan)
    assert pack.is_file()

    protected = prune_repository_packs(repo, older_than_days=0)
    assert protected["removed"] == []
    assert pack.is_file()

    set_repository_pack_pin(repo, receipt.pack_id, pin=False)
    collected = prune_repository_packs(repo, older_than_days=0)
    assert receipt.pack_id in collected["removed"]
    assert not pack.exists()
    rows = list_repository_packs(repo, include_tombstoned=True)
    row = next(item for item in rows if item["pack_id"] == receipt.pack_id)
    assert row["state"] == "tombstoned"
    assert row.get("cleanup_receipt_id")


def test_repository_pack_status_rejects_byte_drift(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "one\n")
    pack = tmp_path / "pack.tar"
    receipt = create_repository_pack(plan_repository_pack(RepositoryPackRequest(
        repositories=(str(repo),), output=str(pack), mode=RepositoryPackMode.TRACKED,
        compression=RepositoryPackCompression.TAR,
    )))
    pack.write_bytes(pack.read_bytes() + b"tamper")
    status = repository_pack_status(repo, receipt.pack_id)
    assert status["exists"] is True
    assert status["verified"] is False
    assert status["current_sha256"] != status["sha256"]


def test_repository_pack_prune_preserves_byte_drift_for_reconciliation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "one\n")
    pack = tmp_path / "drifted.tar"
    receipt = create_repository_pack(plan_repository_pack(RepositoryPackRequest(
        repositories=(str(repo),), output=str(pack), mode=RepositoryPackMode.TRACKED,
        compression=RepositoryPackCompression.TAR,
    )))
    set_repository_pack_pin(repo, receipt.pack_id, pin=False)
    original = pack.read_bytes()
    pack.write_bytes(original + b"tamper")

    result = prune_repository_packs(repo, older_than_days=0)

    assert result["removed"] == []
    assert receipt.pack_id in result["protected"]
    assert pack.is_file(), "unverified repository pack bytes must survive GC for manual reconciliation"
    row = next(item for item in list_repository_packs(repo, include_tombstoned=True) if item["pack_id"] == receipt.pack_id)
    assert row["state"] == "published"
    receipts = list((repo / ".devtool" / "private" / "cleanup" / "receipts").glob("*.json"))
    assert receipts, "protected GC decision must persist a LIFE cleanup receipt"
    receipt_payload = json.loads(receipts[-1].read_text(encoding="utf-8"))
    reasons = [item.get("reason", "") for item in receipt_payload.get("results", [])]
    assert any("registered sha256" in reason for reason in reasons)


def test_backup_shaped_user_data_is_not_archived_but_owned_backup_is(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "one\n")
    user_backup = repo / ".personal-backup"
    user_backup.mkdir()
    (user_backup / "important.txt").write_text("mine", encoding="utf-8")
    owned = repo / ".diagnostic-surface-backup-20261003"
    owned.mkdir()
    (owned / "generated.txt").write_text("devtool", encoding="utf-8")

    discovered = discover_backup_candidates(repo)
    assert owned in discovered
    assert user_backup not in discovered

    moves = archive_backup_candidates(repo, stamp="20261003T000000Z")
    assert len(moves) == 1
    assert not owned.exists()
    assert user_backup.is_dir()
    archived = repo / moves[0].destination
    assert archived.is_dir()
    assert (archived / "generated.txt").read_text(encoding="utf-8") == "devtool"
    receipts = list((repo / ".devtool" / "private" / "cleanup" / "receipts").glob("*.json"))
    assert receipts, "canonical LIFE cleanup receipt must physically persist"


def test_exo_history_source_proof_reconstructs_cleanup_protection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "one\n")
    generated = repo / ".devtool" / "cache" / "reusable.bin"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"reusable")
    digest = hashlib.sha256(generated.read_bytes()).hexdigest()
    history = tmp_path / "history"
    monkeypatch.setenv("DEVTOOL_EXECUTION_HISTORY_DIR", str(history))
    doc = {
        "schema_version": 1,
        "execution": {
            "run_id": "run-old",
            "nodes": [{
                "node_id": "node-old",
                "metadata": {"reuse_source_proof_refs": [f"file:{generated}#sha256={digest}"]},
            }],
        },
    }
    run_dir = history / "run-old"
    run_dir.mkdir(parents=True)
    (run_dir / "execution.json").write_text(json.dumps(doc), encoding="utf-8")
    descriptor = GeneratedOutputDescriptor.create(
        generated, owner="test", provider="test", category="reusable-output",
        producer_id="node-old", lifecycle="rebuildable", scope=CleanupScope.PROJECT,
    )

    ownership = build_cleanup_ownership_view(repo, [descriptor], policy_generation="r03b-test")
    candidate = compile_cleanup_plan(ownership, scope=CleanupScope.PROJECT).candidates[0]
    assert candidate.selected is False
    assert candidate.retention_state == "protected"
    assert any(ref.startswith("execution-history:run-old:reuse-proof:") for ref in candidate.reference_ids)

    # Derived protection is reconstructible from canonical history and vanishes
    # only when that canonical source proof itself is removed.
    (run_dir / "execution.json").unlink()
    rebuilt = build_cleanup_ownership_view(repo, [descriptor], policy_generation="r03b-test")
    candidate2 = compile_cleanup_plan(rebuilt, scope=CleanupScope.PROJECT).candidates[0]
    assert candidate2.selected is True


def test_post_apply_artifact_cleanup_lowers_through_life_and_protects_tracked_source(tmp_path: Path) -> None:
    from devtool.cli import _delete_original_artifact_after_apply
    from devtool.models import OperationResult

    repo = _repo(tmp_path)
    _commit(repo, "one\n")
    external = tmp_path / "overlay.zip"
    external.write_bytes(b"overlay")
    result = OperationResult(
        "apply-overlay", ["apply-overlay", str(external)], 0, 0.01, "applied", [],
        metadata={"artifact_path": str(external), "delete_artifact_on_success": True},
    )
    cleanup = _delete_original_artifact_after_apply([result], repo_root=repo)
    assert cleanup is not None and cleanup.returncode == 0
    assert cleanup.metadata["cleanup_authority"] == "LIFE"
    assert cleanup.metadata["cleanup_receipt_id"].startswith("cleanup-receipt:")
    assert Path(cleanup.metadata["cleanup_receipt_path"]).is_file()
    assert not external.exists()

    tracked = repo / "tracked-artifact.zip"
    tracked.write_bytes(b"tracked")
    _git(repo, "add", tracked.name)
    _git(repo, "commit", "-q", "-m", "tracked artifact")
    tracked_result = OperationResult(
        "apply-overlay", ["apply-overlay", str(tracked)], 0, 0.01, "applied", [],
        metadata={"artifact_path": str(tracked), "delete_artifact_on_success": True},
    )
    refused = _delete_original_artifact_after_apply([tracked_result], repo_root=repo)
    assert refused is not None and refused.returncode == 1
    assert refused.metadata["cleanup_authority"] == "LIFE"
    assert tracked.is_file(), "tracked source must not be deleted merely because apply cleanup was requested"
    assert "tracked source is protected" in refused.output


def test_log_retention_uses_life_and_preserves_run_referenced_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import time
    from devtool import logstore
    from devtool.core_runtime import private_run_root
    from devtool.run_store import AttachmentDescriptor, RunCatalog

    repo = _repo(tmp_path)
    _commit(repo, "one\n")
    logs = tmp_path / "logs"
    logs.mkdir()
    referenced = logs / "referenced.log"
    disposable = logs / "disposable.log"
    referenced.write_text("run evidence", encoding="utf-8")
    disposable.write_text("old housekeeping", encoding="utf-8")
    old = time.time() - 10 * 86400
    os.utime(referenced, (old, old))
    os.utime(disposable, (old, old))

    catalog = RunCatalog.open(private_run_root(repo))
    catalog.add_run({"schema_version": 3, "run_id": "run-log", "status": "succeeded"})
    catalog.add_attachment(AttachmentDescriptor.from_file(
        run_id="run-log", path=referenced, kind="log", privacy="private"
    ))
    catalog.save()

    monkeypatch.setenv("DEVTOOL_REPO_ROOT", str(repo))
    monkeypatch.setenv("DEVTOOL_LOG_MAX_AGE_DAYS", "1")
    monkeypatch.setenv("DEVTOOL_LOG_MAX_FILES", "100")
    monkeypatch.setenv("DEVTOOL_LOG_MAX_TOTAL_MB", "128")
    logstore._rotate_log_root(logs)

    assert referenced.is_file(), "RUN-referenced durable log must survive retention"
    assert not disposable.exists(), "unreferenced age-selected log should be removed through LIFE"


def _ssh_delegation_link():
    from devtool.distributed_execution import DelegationLink, ExecutionRunLink
    return DelegationLink(
        run_link=ExecutionRunLink("parent-run", "parent-node", "child-run"),
        repository="child", target="app", child_contract="workflow:check",
        contract_ref="child::app::workflow:check", federation_ref="child::app::check",
        repository_fingerprint="repo-fingerprint", source_lock_digest="source-lock",
        source_equivalence_digest="source-equivalence",
        provider_identity={
            "provider_id": "tablet", "provider_kind": "ssh", "profile": "tablet",
            "execution_environment": "ssh", "provider_evidence_digest": "provider-evidence",
        },
        attestation_digest="attestation",
    )


def test_ssh_provider_event_and_control_paths_use_remote_worker_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from devtool.core.events import EventBus, RunFinished, RunStarted
    from devtool.core.events.listeners import event_to_dict
    from devtool.distributed_execution import ExternalEventIngestor
    from devtool.distributed_provider_control import dispatch_ssh_provider_control, ssh_provider_event_channel
    from devtool.distributed_transport import DelegatedEventReceiver, TransportProtocolCapabilities

    link = _ssh_delegation_link()
    events = [
        RunStarted(
            run_id="child-run", command="remote",
            metadata={
                "dist_transport_peer": "ssh-remote-devtool",
                "dist_transport_capabilities": TransportProtocolCapabilities().as_dict(),
            },
        ),
        RunFinished(run_id="child-run", status="succeeded", exit_code=0),
    ]
    for seq, event in enumerate(events, start=1):
        event.complete_envelope(sequence=seq)
    journal = "\n".join(json.dumps(event_to_dict(event), sort_keys=True) for event in events) + "\n"
    config = SimpleNamespace(profile="tablet", destination="termux@tablet")
    reads: list[tuple[str, str]] = []

    def read_remote(_config, *, run_id="", session="", node_id=""):
        reads.append((run_id, session))
        return {
            "kind": "ssh-semantic-run-journal", "run_id": "child-run", "child_run_id": "child-run",
            "session": "devtool-child-session", "delegation_id": link.delegation_id,
            "provider_session_binding_id": "", "journal": journal,
        }

    cancelled: list[tuple[str, str]] = []
    def cancel_remote(_config, *, run_id="", session="", node_id=""):
        cancelled.append((run_id, session))
        return {"ok": True, "cancelled": [{"run_id": run_id, "session": session}]}

    monkeypatch.setattr("devtool.ssh_resilience.read_remote_run_journal", read_remote)
    monkeypatch.setattr("devtool.ssh_resilience.cancel_remote_run", cancel_remote)
    monkeypatch.setattr(
        "devtool.ipc.request_run_cancel",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("SSH provider control must not fall back to local IPC")),
    )

    bus = EventBus()
    receiver = DelegatedEventReceiver(ExternalEventIngestor(bus), link=link)
    channel = ssh_provider_event_channel(
        config, link=link, cursor_path=tmp_path / "cursor.json"
    )
    acks = channel.catch_up(receiver)
    assert len(acks) == 2
    assert receiver.acknowledged_sequence == 2
    assert channel.binding.session_id == "devtool-child-session"
    assert reads[0] == ("child-run", "")
    assert reads[-1] == ("child-run", "devtool-child-session")

    ack = dispatch_ssh_provider_control(config, link=link, reason="parent cancelled", requested_by_run_id="parent-run")
    assert ack.accepted
    assert ack.provider_session_binding_id
    assert cancelled == [("child-run", "devtool-child-session")]


def test_proot_stage_cleanup_releases_provider_resource_and_uses_life(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.models import CommandSpec
    from devtool.proot_distro_runner import _register_proot_stage_resource, cleanup_proot_staging
    from devtool.provider_resource_registry import provider_resource_registry_path

    repo = _repo(tmp_path)
    _commit(repo, "source\n")
    stage = tmp_path / "proot-stage"
    stage.mkdir()
    (stage / "secret-env.sh").write_text("secret", encoding="utf-8")
    run_id = "run-proot-stage"
    session = "proot-stage:unit"
    monkeypatch.setenv("DEVTOOL_RUN_ID", run_id)
    resource_id = _register_proot_stage_resource(repo, stage, run_id=run_id, session=session)

    warnings = cleanup_proot_staging([
        CommandSpec(
            "phase", ["true"], cwd=str(repo),
            metadata={
                "proot_stage_owned": True,
                "proot_stage_dir": str(stage),
                "proot_repository_root": str(repo),
                "proot_run_id": run_id,
                "proot_provider_session": session,
            },
        )
    ])

    assert warnings == []
    assert not stage.exists(), "completed PRoot provider staging must be removed through LIFE"
    registry = json.loads(provider_resource_registry_path(repo).read_text(encoding="utf-8"))
    assert registry["resources"][resource_id]["state"] == "released"
    receipts = list((repo / ".devtool" / "private" / "cleanup" / "receipts").glob("*.json"))
    assert receipts, "provider stage cleanup must persist a LIFE receipt"


def test_proot_stage_cleanup_without_repository_authority_retains(tmp_path: Path) -> None:
    from devtool.models import CommandSpec
    from devtool.proot_distro_runner import cleanup_proot_staging

    stage = tmp_path / "orphan-proot-stage"
    stage.mkdir()
    warnings = cleanup_proot_staging([
        CommandSpec("phase", ["true"], metadata={"proot_stage_owned": True, "proot_stage_dir": str(stage)})
    ])

    assert stage.is_dir()
    assert warnings and "repository cleanup authority is unavailable" in warnings[0]


def test_ssh_command_return_fragment_refuses_shell_destructive_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.ssh_returns import SSHReturnBundle, shell_return_fragment
    from devtool.ssh_tmux_runner import SSHRunnerConfig

    repo = tmp_path / "return" / "repo"
    root = repo.parent
    manifest = root / "manifest.json"
    status = root / "transfer.status"
    bundle = SSHReturnBundle(
        run_id="run-ssh-return",
        session="phase",
        root=root,
        repo_root=repo,
        project_root=repo,
        manifest_path=manifest,
        status_path=status,
        remote_root="/remote/repo",
        patterns=("build/outputs/***",),
        source_sync_id="sync",
    )
    cfg = SSHRunnerConfig(host="worker.example", user="mike", profile="tablet", mode="commands")
    monkeypatch.setenv("DEVTOOL_RSYNC_COMMAND", "/bin/echo")

    fragment = shell_return_fragment(cfg, bundle, remote_rsync="rsync")

    assert "rm -rf --" not in fragment
    assert "blocked-existing-local-mirror" in fragment
    assert "Run SSH return recovery so LIFE/RUN/RER can revalidate" in fragment


def test_oci_definition_staging_refuses_destructive_cache_replacement() -> None:
    from devtool.proot_oci import _oci_stage_definition_script

    script = _oci_stage_definition_script("a" * 64, "b" * 64)

    assert "rm -rf" not in script
    assert "refusing destructive replacement outside LIFE/provider reconciliation" in script
    assert 'if [ -d "$dst" ]' in script
    assert 'mktemp -d "$root/.definition-work.XXXXXX"' in script


def test_termux_chroot_sensitive_staging_registers_provider_resource_and_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from devtool.models import CommandSpec
    from devtool.provider_resource_registry import provider_resource_registry_path, release_provider_resources
    from devtool.termux_chroot_runner import wrap_specs_for_termux_host_chroot

    repo = _repo(tmp_path)
    _commit(repo, "source\n")
    monkeypatch.setattr(
        "devtool.termux_chroot_runner.resolve_chroot_identity",
        lambda *args, **kwargs: SimpleNamespace(user="mike", home="/home/mike", shell="/bin/bash"),
    )
    monkeypatch.setattr(
        "devtool.termux_chroot_runner.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    env = {
        "DEVTOOL_RUN_ID": "run-chroot-secret",
        "DEVTOOL_CHROOTCTL": "chrootctl",
        "DEVTOOL_CHROOT_SUDO": "",
    }
    wrapped = wrap_specs_for_termux_host_chroot(
        [CommandSpec("phase", ["echo", "ok"], cwd=str(repo), env={"API_TOKEN": "secret"})],
        "androidos",
        repo_root=repo,
        env=env,
        path_map=[(str(repo.resolve()), "/home/mike/repo")],
    )[0]

    metadata = wrapped.metadata
    assert metadata["chroot_provider_resource_registry"] == "LIFE"
    assert metadata["chroot_provider_resource_id"]
    assert metadata["chroot_provider_session"].startswith("termux-chroot:run-chroot-secret:")
    registry = json.loads(provider_resource_registry_path(repo).read_text(encoding="utf-8"))
    row = registry["resources"][metadata["chroot_provider_resource_id"]]
    assert row["state"] == "active"
    assert row["descriptor"]["category"] == "provider-secret-staging"
    assert "release_provider_resources" in " ".join(wrapped.argv)

    released = release_provider_resources(
        repo,
        run_id="run-chroot-secret",
        session=metadata["chroot_provider_session"],
        reason="unit release",
    )
    assert metadata["chroot_provider_resource_id"] in released
    registry = json.loads(provider_resource_registry_path(repo).read_text(encoding="utf-8"))
    assert registry["resources"][metadata["chroot_provider_resource_id"]]["state"] == "released"


def test_federation_child_terminal_relation_releases_provider_resources(tmp_path: Path) -> None:
    from devtool.distributed_provider_control import provider_cleanup_descriptors, record_delegation_relation
    from devtool.provider_resource_registry import provider_resource_registry_path, register_provider_resources

    repo = _repo(tmp_path)
    _commit(repo, "source\n")
    link = _ssh_delegation_link()
    descriptors = provider_cleanup_descriptors(
        destination="termux@tablet",
        provider="ssh",
        run_id=link.child_run_id,
        session="devtool-child-session",
        stage_path="~/.cache/devtool/ssh-stage/session/op",
        state_path="~/.cache/devtool/ssh-runs/child/session",
        cursor_path="~/.cache/devtool/ssh-runs/child/session/event-cursor.json",
        delegation_id=link.delegation_id,
    )
    resource_ids = set(register_provider_resources(repo, descriptors))

    record_delegation_relation(repo, link, delivery_health="reconciled")

    registry = json.loads(provider_resource_registry_path(repo).read_text(encoding="utf-8"))
    assert resource_ids
    assert {registry["resources"][rid]["state"] for rid in resource_ids} == {"released"}
