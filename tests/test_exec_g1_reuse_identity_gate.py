from __future__ import annotations

import subprocess
from pathlib import Path

from devtool.core.execution_semantics import NodeDefinitionIdentity
from devtool.core.reuse import (
    ExecutionEnvironmentFingerprint,
    PortabilityClass,
    fingerprint_for_spec,
    output_manifest_for_spec,
    qualify_candidate,
)
from devtool.models import CommandSpec


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "exec-g1@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "EXEC-G1"], cwd=path, check=True)
    (path / "a.txt").write_text("a0\n", encoding="utf-8")
    (path / "b.txt").write_text("b0\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt", "b.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True)
    return path


def _candidate(fp: dict, output: dict, *, run: str = "run-old", node: str = "node-old") -> dict:
    return {
        "schema_version": 2,
        "node_definition_id": fp["node_definition_id"],
        "source_run_id": run,
        "source_node_id": node,
        "execution_status": "succeeded",
        "qualification_complete": True,
        "reusable": True,
        "fingerprint": fp,
        "output_manifest": output,
        "result_identity": "result:" + fp["digest"][:16],
        "evidence_refs": [],
    }


def test_stable_node_identity_ignores_run_and_presentation_metadata_but_tracks_semantics(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    base = CommandSpec(
        "compile",
        ["tool", "compile", "--mode", "debug"],
        cwd=str(root),
        metadata={"runner": "command", "run_id": "run-a", "presentation": "verbose"},
    )
    same = CommandSpec(
        "compile",
        ["tool", "compile", "--mode", "debug"],
        cwd=str(root),
        metadata={"runner": "command", "run_id": "run-b", "presentation": "quiet"},
    )
    changed = CommandSpec(
        "compile",
        ["tool", "compile", "--mode", "release"],
        cwd=str(root),
        metadata={"runner": "command", "run_id": "run-c"},
    )
    assert NodeDefinitionIdentity.from_spec(base).value == NodeDefinitionIdentity.from_spec(same).value
    assert NodeDefinitionIdentity.from_spec(base).value != NodeDefinitionIdentity.from_spec(changed).value


def test_semantic_definition_change_invalidates_prior_candidate(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    old = CommandSpec("compile", ["tool", "compile", "--mode", "debug"], cwd=str(root), metadata={"workflow_inputs": ["a.txt"]})
    new = CommandSpec("compile", ["tool", "compile", "--mode", "release"], cwd=str(root), metadata={"workflow_inputs": ["a.txt"]})
    old_fp, _, _ = fingerprint_for_spec(old, repo_root=root)
    new_fp, _, _ = fingerprint_for_spec(new, repo_root=root)
    result = qualify_candidate(new_fp.to_dict(), _candidate(old_fp.to_dict(), output_manifest_for_spec(old).to_dict()), cwd=root)
    assert result.disposition == "invalidated"
    assert "invocation_digest" in result.diff.changed or "node_definition_id" in result.diff.changed


def test_isolated_input_change_preserves_independent_branch_identity(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    left = CommandSpec("left", ["tool", "left"], cwd=str(root), metadata={"workflow_inputs": ["a.txt"]})
    right = CommandSpec("right", ["tool", "right"], cwd=str(root), metadata={"workflow_inputs": ["b.txt"]})
    left_before = fingerprint_for_spec(left, repo_root=root)[0]
    right_before = fingerprint_for_spec(right, repo_root=root)[0]
    (root / "a.txt").write_text("a1\n", encoding="utf-8")
    left_after = fingerprint_for_spec(left, repo_root=root)[0]
    right_after = fingerprint_for_spec(right, repo_root=root)[0]
    assert left_before.digest != left_after.digest
    assert right_before.digest == right_after.digest


def test_relevant_environment_toolchain_and_safe_secret_identity_invalidate_without_storing_secret(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    common = {
        "workflow_inputs": ["a.txt"],
        "reuse_secret_identities": ["vault:key@v1"],
        "environment_domains": {"python": "3.13.7", "compiler": "clang-20"},
        "execution_environment": "native-termux",
        "reuse_portability": "same-environment",
    }
    a = CommandSpec("secure", ["tool", "secure"], cwd=str(root), env={"API_TOKEN": "raw-secret-A"}, metadata=dict(common))
    b = CommandSpec("secure", ["tool", "secure"], cwd=str(root), env={"API_TOKEN": "raw-secret-B"}, metadata={**common, "reuse_secret_identities": ["vault:key@v2"]})
    c = CommandSpec("secure", ["tool", "secure"], cwd=str(root), env={"API_TOKEN": "raw-secret-C"}, metadata={**common, "environment_domains": {"python": "3.14.0", "compiler": "clang-20"}})
    fa, ca, _ = fingerprint_for_spec(a, repo_root=root)
    fb, cb, _ = fingerprint_for_spec(b, repo_root=root)
    fc, _, _ = fingerprint_for_spec(c, repo_root=root)
    assert fa.environment_digest != fb.environment_digest
    assert fa.environment_digest != fc.environment_digest
    serialized = str(ca.to_dict()) + str(cb.to_dict())
    assert "raw-secret-A" not in serialized and "raw-secret-B" not in serialized


def test_portability_axes_cover_termux_chroot_desktop_and_ssh_provider_contexts() -> None:
    termux = ExecutionEnvironmentFingerprint(domains={"python": "3.13"}, execution_environment="native-termux", portability=PortabilityClass.SAME_ENVIRONMENT)
    chroot = ExecutionEnvironmentFingerprint(domains={"python": "3.13"}, execution_environment="chroot:androidos", portability=PortabilityClass.SAME_ENVIRONMENT)
    desktop_a = ExecutionEnvironmentFingerprint(domains={"python": "3.13"}, execution_environment="desktop-linux", portability=PortabilityClass.PORTABLE)
    desktop_b = ExecutionEnvironmentFingerprint(domains={"python": "3.13"}, execution_environment="native-termux", portability=PortabilityClass.PORTABLE)
    ssh_a = ExecutionEnvironmentFingerprint(domains={"python": "3.13"}, provider_identity="ssh:tablet", portability=PortabilityClass.SAME_PROVIDER)
    ssh_b = ExecutionEnvironmentFingerprint(domains={"python": "3.13"}, provider_identity="ssh:desktop", portability=PortabilityClass.SAME_PROVIDER)
    assert termux.digest != chroot.digest
    assert desktop_a.digest == desktop_b.digest
    assert ssh_a.digest != ssh_b.digest
