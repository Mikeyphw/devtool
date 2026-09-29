from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import zipfile

import pytest

from devtool.artifact_inspection import inspect_artifact
from devtool.artifact_lifecycle import (
    ArtifactEnvelopeResolver,
    AssuranceLevel,
    GenerationOwner,
    LifecycleGenerationLedger,
    MutationKind,
    UnsafeAuthorization,
)
from devtool.artifact_trust import (
    SecretProviderRef,
    SigningEvidence,
    SigningIdentityRecord,
    SigningIdentityRegistry,
    SigningIdentityId,
    TrustPolicy,
    merge_trust_policy,
)
from devtool.artifacts import write_archive_entries
from devtool.repository_pack import (
    RepositoryPackCompression,
    RepositoryPackError,
    RepositoryPackMode,
    RepositoryPackRequest,
    create_repository_pack,
    plan_repository_pack,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Devtool Tests")
    _git(root, "config", "user.email", "devtool@example.invalid")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-qm", "base")
    return root


def _overlay(path: Path, *, repo_name: str, assurance: str = "qualified", validation: dict | None = None) -> Path:
    payload = b"new-content\n"
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "name": "life-x02-fixture",
        "assurance": assurance,
        "repository": {"name": repo_name},
        "apply": {"atomic": True, "commit": {"enabled": True, "message": "fixture"}},
        "inventory": {
            "format": 1,
            "entries": [
                {"path": "new.txt", "action": "add", "sha256": _sha(payload), "size": len(payload)},
            ],
        },
        "validation": validation or {"required": True, "tests": [{"ref": "fixture-test"}], "maximum_warnings": 0},
    }
    write_archive_entries(path, {".devtool-artifact.json": json.dumps(manifest).encode(), "new.txt": payload})
    return path


def test_envelope_separates_semantic_content_transport_and_schema_assurance(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    a = _overlay(tmp_path / "a.zip", repo_name=repo.name)
    b = _overlay(tmp_path / "b.zip", repo_name=repo.name)
    # Repack B with an extra transport-only ZIP comment; semantic manifest/content stay unchanged.
    with zipfile.ZipFile(b, "a") as zf:
        zf.comment = b"different transport bytes"
    resolver = ArtifactEnvelopeResolver()
    ea = resolver.resolve(a, repo_root=repo)
    eb = resolver.resolve(b, repo_root=repo)
    assert ea.identities.semantic_sha256 == eb.identities.semantic_sha256
    assert ea.identities.content_sha256 == eb.identities.content_sha256
    assert ea.identities.transport_sha256 != eb.identities.transport_sha256
    assert ea.assurance is AssuranceLevel.QUALIFIED

    legacy = _overlay(tmp_path / "legacy.zip", repo_name=repo.name, assurance="")
    assert resolver.resolve(legacy).assurance is AssuranceLevel.LEGACY


def test_qualification_accumulates_blockers_and_unsafe_authorization_is_typed(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    artifact = _overlay(
        tmp_path / "bad.zip",
        repo_name="other-repo",
        assurance="",
        validation={"required": True, "mode": "required"},
    )
    resolver = ArtifactEnvelopeResolver()
    envelope = resolver.resolve(artifact)
    receipt = resolver.qualify(envelope, repo_root=repo, public_runner=True)
    codes = {item.code for item in receipt.blockers}
    assert "legacy-assurance-public-runner" in codes
    assert "repository-name-mismatch" in codes
    assert "required-validation-unresolved" in codes
    assert not receipt.qualified

    authorized = resolver.qualify(
        envelope,
        repo_root=repo,
        public_runner=True,
        unsafe_authorizations=[UnsafeAuthorization("allow-legacy-assurance", "fixture", "pytest")],
    )
    assert "legacy-assurance-public-runner" not in {item.code for item in authorized.blockers}
    assert authorized.unsafe_authorizations[0].provenance == "pytest"


def test_plan_contains_mutation_validation_expected_tree_and_leases(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    artifact = _overlay(tmp_path / "overlay.zip", repo_name=repo.name)
    resolver = ArtifactEnvelopeResolver()
    plan = resolver.resolve_qualified_plan(artifact, repo_root=repo, public_runner=False)
    assert plan.executable
    assert len(plan.mutations.mutations) == 1
    mutation = plan.mutations.mutations[0]
    assert mutation.path == "new.txt"
    assert mutation.kind is MutationKind.ADD
    assert plan.expected_final_tree.entries[0].sha256 == mutation.expected_sha256
    assert plan.validation.required
    assert plan.validation.test_refs == ("fixture-test",)
    assert plan.validation.maximum_warnings == 0
    assert plan.write_leases[0].generations == (1, 2, 3, 4, 5)

    inspected = inspect_artifact(artifact, repo_root=repo)
    assert inspected["canonical"]["plan"]["plan_id"] == plan.plan_id


def test_generation_ledger_is_monotonic_and_tracks_latest_devtool_owned_generation() -> None:
    ledger = LifecycleGenerationLedger()
    ledger.baseline("a.txt", "g0")
    ledger.advance("a.txt", GenerationOwner.PAYLOAD, "g1")
    ledger.advance("a.txt", GenerationOwner.VALIDATION, "g2")
    assert ledger.paths["a.txt"].generation == 2
    with pytest.raises(ValueError):
        ledger.advance("a.txt", GenerationOwner.PAYLOAD, "stale")


def test_trust_policy_merge_is_monotonic() -> None:
    merged = merge_trust_policy(
        TrustPolicy(require_signature=True, minimum_assurance="qualified", allowed_identity_ids=("a", "b")),
        TrustPolicy(require_known_identity=True, minimum_assurance="legacy", allowed_identity_ids=("b", "c")),
        TrustPolicy(minimum_assurance="strict", allowed_identity_ids=("b",)),
    )
    assert merged.require_signature
    assert merged.require_known_identity
    assert merged.minimum_assurance == "strict"
    assert merged.allowed_identity_ids == ("b",)


def test_signing_registry_is_private_non_overwriting_and_non_secret(tmp_path: Path) -> None:
    registry_path = tmp_path / "private" / "identities.json"
    registry = SigningIdentityRegistry(registry_path)
    record = SigningIdentityRecord(
        identity_id=SigningIdentityId("personal-release"),
        provider="android-keystore",
        key_reference="/private/keys/release.jks",
        alias="release",
        certificate_sha256="a" * 64,
        purposes=("debug", "release"),
        secret_provider=SecretProviderRef("fd", "DEVTOOL_SIGNING_FD"),
    )
    registry.register(record)
    assert registry.get("personal-release") == record
    text = registry_path.read_text(encoding="utf-8")
    assert "password" not in text.lower()
    assert "passphrase" not in text.lower()
    if os.name == "posix":
        assert registry_path.stat().st_mode & 0o077 == 0
    changed = SigningIdentityRecord(
        identity_id=SigningIdentityId("personal-release"),
        provider="other",
        key_reference="different",
        alias="release",
        certificate_sha256="b" * 64,
    )
    with pytest.raises(FileExistsError):
        registry.register(changed)
    evidence = SigningEvidence.create(
        identity=record,
        artifact_sha256="c" * 64,
        variant="release",
        verification_tool="apksigner",
        verification_result="verified",
    )
    assert evidence.identity_id == "personal-release"
    assert "secret" not in json.dumps(evidence.as_dict()).lower()


def test_worktree_pack_preserves_dirty_and_untracked_bytes_and_raw_git_is_excluded(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("dirty-worktree\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    output = tmp_path / "pack.tar.gz"
    request = RepositoryPackRequest(
        repositories=(str(repo),), output=str(output), mode=RepositoryPackMode.WORKTREE,
        compression=RepositoryPackCompression.TAR_GZ,
    )
    plan = plan_repository_pack(request)
    receipt = create_repository_pack(plan)
    assert receipt.verified and receipt.published
    prefix = plan.repositories[0].identity.fingerprint[:20]
    with tarfile.open(output, "r:gz") as archive:
        names = {item.name for item in archive.getmembers() if item.isfile()}
        tracked = archive.extractfile(f"repositories/{prefix}/tracked.txt")
        untracked = archive.extractfile(f"repositories/{prefix}/untracked.txt")
        assert tracked and tracked.read() == b"dirty-worktree\n"
        assert untracked and untracked.read() == b"untracked\n"
        assert not any(name.startswith(".git/") or "/.git/" in name for name in names)
        assert "devtool-repository-pack.json" in names


def test_tracked_and_commit_modes_are_distinct_from_worktree(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("worktree-after-index\n", encoding="utf-8")
    outputs = {}
    for mode in (RepositoryPackMode.WORKTREE, RepositoryPackMode.TRACKED, RepositoryPackMode.COMMIT):
        output = tmp_path / f"{mode.value}.zip"
        plan = plan_repository_pack(RepositoryPackRequest(
            repositories=(str(repo),), output=str(output), mode=mode, compression=RepositoryPackCompression.ZIP,
        ))
        create_repository_pack(plan)
        prefix = plan.repositories[0].identity.fingerprint[:20]
        with zipfile.ZipFile(output) as archive:
            outputs[mode] = archive.read(f"repositories/{prefix}/tracked.txt")
    assert outputs[RepositoryPackMode.WORKTREE] == b"worktree-after-index\n"
    assert outputs[RepositoryPackMode.TRACKED] == b"dirty\n"
    assert outputs[RepositoryPackMode.COMMIT] == b"base\n"


def test_pack_source_stability_rejects_changes_after_plan(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    output = tmp_path / "pack.zip"
    plan = plan_repository_pack(RepositoryPackRequest(
        repositories=(str(repo),), output=str(output), mode=RepositoryPackMode.WORKTREE,
        compression=RepositoryPackCompression.ZIP,
    ))
    (repo / "tracked.txt").write_text("changed-after-plan\n", encoding="utf-8")
    with pytest.raises(RepositoryPackError, match="source changed"):
        create_repository_pack(plan)
    assert not output.exists()


def test_pack_rejects_output_inside_source_and_preserves_multi_repo_identity(tmp_path: Path) -> None:
    repo1 = _repo(tmp_path / "repo1")
    repo2 = _repo(tmp_path / "repo2")
    with pytest.raises(RepositoryPackError, match="outside source repository"):
        plan_repository_pack(RepositoryPackRequest(repositories=(str(repo1),), output=str(repo1 / "pack.zip")))

    plan = plan_repository_pack(RepositoryPackRequest(
        repositories=(str(repo1), str(repo2)), output=str(tmp_path / "multi.zip"),
        compression=RepositoryPackCompression.ZIP,
    ))
    assert len(plan.repositories) == 2
    assert plan.repositories[0].identity.fingerprint != plan.repositories[1].identity.fingerprint
    receipt = create_repository_pack(plan)
    assert receipt.published
    with zipfile.ZipFile(receipt.output) as archive:
        manifest = json.loads(archive.read("devtool-repository-pack.json"))
    assert len(manifest["repositories"]) == 2
    assert len({item["identity"]["fingerprint"] for item in manifest["repositories"]}) == 2


def test_pack_is_deterministic_for_same_plan_and_source(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    output = tmp_path / "stable.tar.gz"
    plan = plan_repository_pack(RepositoryPackRequest(
        repositories=(str(repo),), output=str(output), compression=RepositoryPackCompression.TAR_GZ,
    ))
    first = create_repository_pack(plan)
    first_bytes = output.read_bytes()
    second = create_repository_pack(plan)
    assert output.read_bytes() == first_bytes
    assert second.sha256 == first.sha256


def test_bundle_compiler_uses_deterministic_last_writer_semantics(tmp_path: Path) -> None:
    from devtool.artifacts import create_overlay_bundle

    repo = _repo(tmp_path / "repo")
    payload1 = b"one\n"
    payload2 = b"two\n"
    overlays = []
    for index, payload in enumerate((payload1, payload2), 1):
        overlay = tmp_path / f"ov{index}.zip"
        manifest = {
            "schema_version": 2,
            "type": "overlay",
            "assurance": "qualified",
            "repository": {"name": repo.name},
            "inventory": {"format": 1, "entries": [{"path": "same.txt", "action": "add", "sha256": _sha(payload), "size": len(payload)}]},
        }
        write_archive_entries(overlay, {".devtool-artifact.json": json.dumps(manifest).encode(), "same.txt": payload})
        overlays.append(overlay)
    bundle = tmp_path / "bundle.zip"
    entries = {}
    apply_order = []
    for index, overlay in enumerate(overlays, 1):
        data = overlay.read_bytes()
        arcname = f"overlays/{index:03d}_{overlay.name}"
        entries[arcname] = data
        apply_order.append({
            "id": f"ov{index}", "path": arcname, "required": True,
            "sha256": _sha(data), "size": len(data),
        })
    bundle_manifest = {
        "schema_version": 2, "type": "overlay_bundle", "kind": "devtool.overlay_bundle",
        "name": "bundle", "repository": {"name": repo.name},
        "overlays": apply_order, "apply_order": apply_order,
        "apply": {"atomic": True, "conflict_policy": "warn", "path_policy": "repository-relative", "requires_clean_worktree": False},
        "validation": {"required": False, "strategy": "per-overlay", "rollback_on_failure": True},
    }
    entries["devtool-bundle.json"] = json.dumps(bundle_manifest).encode()
    write_archive_entries(bundle, entries)
    resolver = ArtifactEnvelopeResolver()
    envelope = resolver.resolve(bundle)
    qualification = resolver.qualify(envelope, repo_root=repo, public_runner=False)
    plan = resolver.plan(envelope, qualification)
    assert len(plan.mutations.mutations) == 1
    mutation = plan.mutations.mutations[0]
    assert mutation.path == "same.txt"
    assert mutation.expected_sha256 == _sha(payload2)
    assert mutation.source_identity


def test_certificate_continuity_requires_explicit_authorization() -> None:
    from devtool.artifact_trust import assert_certificate_continuity

    identity = SigningIdentityRecord(
        identity_id=SigningIdentityId("android-update"),
        provider="android-keystore",
        key_reference="release.jks",
        alias="release",
        certificate_sha256="a" * 64,
    )
    assert_certificate_continuity(identity, "a" * 64)
    with pytest.raises(ValueError, match="certificate changed"):
        assert_certificate_continuity(identity, "b" * 64)
    assert_certificate_continuity(identity, "b" * 64, allow_change=True)


def test_artifact_execution_record_is_reference_only_and_has_durability() -> None:
    from devtool.artifact_lifecycle import ArtifactExecutionRecord

    record = ArtifactExecutionRecord.create(
        artifact_id="artifact:a",
        plan_id="plan:p",
        qualification_receipt_id="qualification:q",
        validation_evidence_id="validation:v",
        transaction_id="tx:t",
        rollback_receipt_id="rollback:r",
        trust_evidence_ids=("signing:s",),
        final_state_identity="tree:f",
        durability="retained",
    )
    payload = record.as_dict()
    assert payload["durability"] == "retained"
    assert payload["trust_evidence_ids"] == ["signing:s"]
    assert not any(key.endswith("bytes") or "secret" in key for key in payload)


def test_repository_pack_git_bundle_is_verified_and_manifested(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    output = tmp_path / "with-history.zip"
    plan = plan_repository_pack(RepositoryPackRequest(
        repositories=(str(repo),), output=str(output), compression=RepositoryPackCompression.ZIP,
        include_git_bundle=True,
    ))
    receipt = create_repository_pack(plan)
    assert receipt.verified
    prefix = plan.repositories[0].identity.fingerprint[:20]
    with zipfile.ZipFile(output) as archive:
        assert f"git/{prefix}.bundle" in archive.namelist()
        manifest = json.loads(archive.read("devtool-repository-pack.json"))
    assert len(manifest["repositories"][0]["git_bundle_sha256"]) == 64


def test_handoff_pack_requires_explicit_members_and_only_includes_them(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / "handoff.txt").write_text("handoff\n", encoding="utf-8")
    with pytest.raises(RepositoryPackError, match="explicit non-empty member list"):
        plan_repository_pack(RepositoryPackRequest(
            repositories=(str(repo),), output=str(tmp_path / "empty.zip"), mode=RepositoryPackMode.HANDOFF,
        ))
    request = RepositoryPackRequest(
        repositories=(str(repo),), output=str(tmp_path / "handoff.zip"), mode=RepositoryPackMode.HANDOFF,
        compression=RepositoryPackCompression.ZIP,
        handoff_members={str(repo): ("handoff.txt",)},
    )
    plan = plan_repository_pack(request)
    receipt = create_repository_pack(plan)
    prefix = plan.repositories[0].identity.fingerprint[:20]
    with zipfile.ZipFile(receipt.output) as archive:
        names = set(archive.namelist())
    assert f"repositories/{prefix}/handoff.txt" in names
    assert f"repositories/{prefix}/tracked.txt" not in names


def test_life_x02_cli_surfaces_parse_canonical_commands() -> None:
    from devtool.cli import build_parser

    parser = build_parser()
    delivery = parser.parse_args(["delivery", "plan", "overlay.zip", "--public-runner", "--unsafe-authorize", "allow-legacy-assurance:test"])
    assert delivery.command == "delivery"
    assert delivery.delivery_command == "plan"
    assert delivery.public_runner
    pack = parser.parse_args(["repo-pack", "plan", "/repo/a", "/repo/b", "-o", "/tmp/repos.tar.gz", "--mode", "tracked"])
    assert pack.command == "repo-pack"
    assert pack.repositories == ["/repo/a", "/repo/b"]
    signing = parser.parse_args(["signing-identity", "register", "personal", "--provider", "android", "--key-reference", "key.jks"])
    assert signing.command == "signing-identity"
    assert signing.identity_id == "personal"


def test_ephemeral_signing_secret_staging_is_private_and_always_cleaned(tmp_path: Path) -> None:
    from devtool.artifact_trust import stage_ephemeral_secret

    staged_path = None
    with pytest.raises(RuntimeError):
        with stage_ephemeral_secret(b"super-secret", directory=tmp_path / "stage") as path:
            staged_path = path
            assert path.read_bytes() == b"super-secret"
            if os.name == "posix":
                assert path.stat().st_mode & 0o077 == 0
            raise RuntimeError("cancel")
    assert staged_path is not None
    assert not staged_path.exists()


def test_repo_pack_cli_machine_contract_wraps_command_payload_under_data(tmp_path: Path, capsys) -> None:
    from devtool import cli

    repo = _repo(tmp_path / "repo")
    output = tmp_path / "pack.tar.gz"
    rc = cli.main([
        "-r", str(repo), "repo-pack", "plan", str(repo),
        "-o", str(output), "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "repo-pack"
    assert payload["data"]["plan_id"].startswith("pack-plan:")
    assert payload["data"]["repositories"][0]["root"] == str(repo.resolve())
