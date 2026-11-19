"""M2 source preflight and external PYZ execution refusal regression.

Tests create isolated local Git worktrees. No GitHub traffic, mutations of real
repositories or untrusted archive entrypoint are permitted.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import os
import subprocess
import sys
import zipfile

import pytest

from devtool.native_runtime_preflight import (
    NativePreflightError, attest_runtime, preflight_manifest, resolve_runtime,
    preverify_archive, run_verified_pyz, verify_target,
)

REPOSITORY = "git@github.com:Mikeyphw/devtool.git"
MINIMUM = {"minimum": "3.2.0", "major": 3}


def git(root: Path, *argv: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", str(root), *argv], capture_output=True,
                       text=True, check=False)
    if check:
        assert p.returncode == 0, (argv, p.stderr)
    return p.stdout.strip()


def repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    root.mkdir()
    git(root, "init", "-b", "feature/native-m2-test")
    git(root, "config", "user.name", "M2 Test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "remote", "add", "origin", REPOSITORY)
    return root


def add_runtime(root: Path, version: str = "3.2.0") -> None:
    directory = root / "devtool"
    directory.mkdir()
    (directory / "launcher_contract_v3.py").write_text(
        "LAUNCHER_CONTRACT_VERSION = " + repr(version) + "\n", encoding="utf-8")
    (directory / "core_cli_schema.json").write_text("{}\n", encoding="utf-8")
    # Runtime module is a committed object, not a mutable unchecked import.
    (directory / "native_runtime_preflight.py").write_text(
        Path(__import__("devtool.native_runtime_preflight", fromlist=["__file__"]).__file__).read_text(),
        encoding="utf-8")
    git(root, "add", "devtool")
    git(root, "commit", "-m", "test: runtime")


def source_manifest(target: Path) -> dict:
    return {
        "schema_version": 3,
        "base_commit": git(target, "rev-parse", "HEAD"),
        "base_tree": git(target, "rev-parse", "HEAD^{tree}"),
        "repository": {"remote": REPOSITORY, "branch": "feature/native-m2-test",
                       "enforce_remote": True, "enforce_branch": True},
        "delivery_contract": {"runtime": {"compatibility": MINIMUM}},
    }


@pytest.fixture
def pair(tmp_path):
    target = repo(tmp_path, "target")
    (target / "README.md").write_text("original\n", encoding="utf-8")
    git(target, "add", "README.md")
    git(target, "commit", "-m", "base")
    runtime = repo(tmp_path, "runtime")
    add_runtime(runtime)
    return target, runtime


def test_attested_named_target_and_separate_trusted_runtime(pair):
    target, runtime = pair
    result = attest_runtime(runtime, MINIMUM)
    assert result.version == "3.2.0"
    assert result.module_blob == git(runtime, "rev-parse", "HEAD:devtool/launcher_contract_v3.py")
    assert result.schema_blob == git(runtime, "rev-parse", "HEAD:devtool/core_cli_schema.json")
    assert result.preflight_blob == git(runtime, "rev-parse", "HEAD:devtool/native_runtime_preflight.py")
    assert verify_target(target, source_manifest(target))["branch"] == "feature/native-m2-test"


def test_detached_runtime_allowed_but_detached_target_refused(pair):
    target, runtime = pair
    git(runtime, "checkout", "--detach", "HEAD")
    assert attest_runtime(runtime, MINIMUM).detached
    manifest = source_manifest(target)
    git(target, "checkout", "--detach", "HEAD")
    with pytest.raises(NativePreflightError, match="branch"):
        verify_target(target, manifest)


@pytest.mark.parametrize("which", ["module", "schema", "preflight", "dirty", "origin", "major"])
def test_bad_runtime_refused_before_execution(pair, which):
    _, runtime = pair
    if which == "module":
        (runtime / "devtool/launcher_contract_v3.py").write_text("LAUNCHER_CONTRACT_VERSION='9.0.0'\n")
    if which == "schema":
        (runtime / "devtool/core_cli_schema.json").write_text('{"tampered":true}\n')
    if which == "preflight":
        (runtime / "devtool/native_runtime_preflight.py").write_text("# tampered\n")
    if which == "dirty":
        (runtime / "extra.txt").write_text("untracked\n")
    if which == "origin":
        git(runtime, "remote", "set-url", "origin", "git@github.com:attacker/devtool.git")
    if which == "major":
        (runtime / "devtool/launcher_contract_v3.py").write_text("LAUNCHER_CONTRACT_VERSION='4.0.0'\n")
        git(runtime, "add", ".")
        git(runtime, "commit", "-m", "major drift")
    with pytest.raises((NativePreflightError, ValueError)):
        attest_runtime(runtime, MINIMUM)


def test_latest_compatible_candidate_selected_without_mutation(pair, tmp_path):
    target, a = pair
    b = repo(tmp_path, "newer")
    add_runtime(b, "3.2.4")
    chosen = resolve_runtime(MINIMUM, target=target, trusted=(a, b))
    assert chosen.root == b
    assert chosen.version == "3.2.4"
    assert not git(target, "status", "--porcelain")


def test_explicit_bad_runtime_does_not_silently_fallback(pair, tmp_path):
    target, runtime = pair
    bad = tmp_path / "bad"
    bad.mkdir()
    with pytest.raises(NativePreflightError, match="explicitly trusted"):
        resolve_runtime(MINIMUM, target=target, trusted=(bad, runtime))


def test_runtime_target_overlap_refused(pair):
    target, _ = pair
    with pytest.raises(NativePreflightError, match="separate"):
        resolve_runtime(MINIMUM, target=target, trusted=(target,))


def test_dirty_wrong_head_wrong_tree_and_origin_target_refused(pair):
    target, _ = pair
    manifest = source_manifest(target)
    (target / "extra").write_text("untracked\n")
    with pytest.raises(NativePreflightError, match="dirty"):
        verify_target(target, manifest)
    (target / "extra").unlink()
    wrong = deepcopy(manifest)
    wrong["base_commit"] = "f" * 40
    with pytest.raises(NativePreflightError, match="HEAD/tree"):
        verify_target(target, wrong)
    wrong = deepcopy(manifest)
    wrong["base_tree"] = "f" * 40
    with pytest.raises(NativePreflightError, match="HEAD/tree"):
        verify_target(target, wrong)
    git(target, "remote", "set-url", "origin", "git@github.com:attacker/devtool.git")
    with pytest.raises(NativePreflightError, match="origin mismatch"):
        verify_target(target, manifest)


def test_symlink_target_and_runtime_refused(pair, tmp_path):
    target, runtime = pair
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(NativePreflightError, match="symlink"):
        verify_target(alias, source_manifest(target))
    alias.unlink()
    alias.symlink_to(runtime, target_is_directory=True)
    with pytest.raises(NativePreflightError, match="symlink"):
        attest_runtime(alias, MINIMUM)


def test_runtime_selection_ignores_poisoned_path(pair, tmp_path, monkeypatch):
    target, runtime = pair
    folder = tmp_path / "fake-bin"
    folder.mkdir()
    malicious = folder / "git"
    malicious.write_text("#!/bin/sh\necho FAKE\n", encoding="utf-8")
    malicious.chmod(0o755)
    expected = git(runtime, "rev-parse", "HEAD")
    monkeypatch.setenv("PATH", str(folder))
    assert attest_runtime(runtime, MINIMUM).commit == expected  # fake binary never selected


def test_untrusted_pyz_never_executes_main_before_verification(pair, tmp_path):
    target, runtime = pair
    marker = tmp_path / "PWNED"
    archive = tmp_path / "attack.pyz"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("__main__.py", f"from pathlib import Path\nPath({str(marker)!r}).write_text('PWNED')\n")
        z.writestr(".devtool-artifact.json", "{}")
    with pytest.raises(NativePreflightError, match="source-bound executable v2"):
        run_verified_pyz(target, archive, trusted=(runtime,))
    assert not marker.exists()


def test_legacy_contract_skips_new_gate(pair):
    target, runtime = pair
    assert preflight_manifest(target, {"schema_version": 2}, trusted=(runtime,)) is None


def test_external_check_cannot_treat_malformed_zip_as_pass(pair, tmp_path):
    target, runtime = pair
    archive = tmp_path / "broken.pyz"
    archive.write_bytes(b"not a zip")
    with pytest.raises((NativePreflightError, OSError, ValueError, RuntimeError)):
        preverify_archive(target, archive, trusted=(runtime,))


def test_v3_preflight_selects_attested_runtime_without_mutation(pair, monkeypatch):
    target, runtime = pair
    import devtool.native_delivery_contract_v3 as contracts
    monkeypatch.setattr(contracts, 'normalize_delivery_contract',
                        lambda manifest: {'status': 'declared-not-executed'})
    manifest = source_manifest(target)
    proof = preflight_manifest(target, manifest, trusted=(runtime,))
    assert proof['status'] == 'VERIFIED'
    assert proof['target']['head'] == git(target, 'rev-parse', 'HEAD')
    assert proof['runtime']['commit'] == git(runtime, 'rev-parse', 'HEAD')
    assert proof['mutation_permitted'] is False
    assert proof['ci_qualified'] is False


def test_explicit_runtime_propagates_into_canonical_artifact_gate(pair, tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from devtool import artifacts, native_delivery_contract_v3 as contract
    target, runtime = pair
    manifest = source_manifest(target)
    # Isolate the handoff between canonical artifact qualification and the
    # M2 runtime gate; native archive structure is independently covered by
    # the existing G0A executable-overlay regression tests.
    monkeypatch.setattr(contract, 'normalize_delivery_contract',
                        lambda m: {'status': 'declared-not-executed'})
    archive = tmp_path / 'fixture.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('.devtool-artifact.json', json.dumps(manifest))
    checked = []
    def native_qualification(repo_root, artifact, **kwargs):
        proof = preflight_manifest(repo_root, manifest)
        checked.append(proof['runtime']['root'])
        return SimpleNamespace(ok=True, messages=[])
    monkeypatch.setattr(artifacts, 'validate_artifact', native_qualification)
    proof = preverify_archive(target, archive, trusted=(runtime,))
    assert checked == [str(runtime)]
    assert proof['preflight']['runtime']['root'] == str(runtime)
    assert proof['code_executed'] is False
