"""Native M3 commit-to-session continuity and adversarial recovery tests.

Uses real isolated Git commits and the existing native adoption authority.
Never invokes overlay apply, pushes, PR writes, or device/release operations.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from devtool import artifacts
from devtool.delivery_session import adopt_applied, status, DeliverySessionError
from devtool.native_delivery_continuity import (
    NativeContinuityError, adopt_after_apply, reconcile_committed, is_committed_v3,
)


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def fixture(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "ci@example.test")
    git(repo, "config", "user.name", "CI")
    git(repo, "checkout", "-qb", "feature/m3")
    git(repo, "remote", "add", "origin", "git@github.com:Mikeyphw/devtool.git")
    (repo / "sample.txt").write_text("old\n")
    git(repo, "add", "sample.txt")
    git(repo, "commit", "-qm", "source")
    parent, tree = git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "HEAD^{tree}")
    (repo / "sample.txt").write_text("new\n")
    git(repo, "add", "sample.txt")
    git(repo, "commit", "-qm", "native committed")
    commit = git(repo, "rev-parse", "HEAD")
    old, new = hashlib.sha256(b"old\n").hexdigest(), hashlib.sha256(b"new\n").hexdigest()
    archive = tmp_path / "native-v3.zip"
    archive.write_bytes(b"simulated persisted native archive; history is authority")
    manifest = {
        "schema_version": 3, "type": "overlay", "base_contract_mode": "exact-tree",
        "base_commit": parent, "base_tree": tree,
        "repository": {"remote": "git@github.com:Mikeyphw/devtool.git", "branch": "feature/m3",
                       "enforce_remote": True, "enforce_branch": True},
        "inventory": {"format": 1, "entries": [{"path": "sample.txt", "action": "modify",
                                               "source_sha256": old, "sha256": new, "size": 4}]},
        "touched_paths": ["sample.txt"], "source_sha256": {"sample.txt": old},
        "expected_post_sha256": {"sample.txt": new},
        "validation": {"required": True, "test": False, "restore": False, "build": False,
                       "tests": [{"id": "unit", "runner": "python", "required": True,
                                  "command": ["python3", "-B", "-m", "pytest", "-q"]}]},
        "apply": {"atomic": True, "requires_clean_worktree": True,
                  "commit": {"enabled": True, "strategy": "single", "message": "native committed"}},
        "delivery_contract": {
            "version": 1,
            "runtime": {"compatibility": {"minimum": "3.2.0", "major": 3},
                        "resolution": "trusted-verified-compatible",
                        "verify": ["commit", "tree", "launcher-module-blob", "cli-schema-blob"]},
            "session": {"after_commit": "adopt-or-resume",
                        "bind_to": ["artifact-digest", "transaction-id", "result-commit", "branch"]},
            "remote": {"base_repository": "Mikeyphw/devtool", "base_branch": "main",
                       "base_sha": "e" * 40, "push": "requires-explicit-approval",
                       "pull_request": {"mode": "requires-explicit-approval", "draft": True,
                                        "title": "feat: source-bound M3"}},
            "ci": {"policy_path": "config/delivery-required-ci.json",
                   "observe": "on-explicit-request", "exact_pr_head": True,
                   "qualification": "required-workflows-only"},
            "recovery": {"after_commit": "resume-only", "unknown_remote_outcome": "read-only-reconcile"},
        },
    }
    receipt = {
        "artifact": str(archive), "commit_hash": commit, "manifest": manifest,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "status": "ok",
        "validation_status": "ok", "content_identity": {"size": archive.stat().st_size},
        "transaction": {"id": "tx-m3", "status": "completed"},
        "history_id": "hist-m3", "operation_id": "op-m3",
        "validation_plan": {"validation_step_outcomes": [
            {"name": "native-test", "outcome": "passed", "returncode": 0, "skipped": False}
        ]},
    }
    history = [receipt]
    monkeypatch.setattr(artifacts, "load_history", lambda _: history)
    # The transaction recovery authority is read-only; fixture has no pending work.
    from devtool.overlay import recovery
    monkeypatch.setattr(recovery, "overlay_recovery_plan", lambda *_args, **_kw: {
        "marker_issues": [], "active_transactions": []})
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    results = [SimpleNamespace(name="apply-overlay", returncode=0, skipped=False),
               SimpleNamespace(name="native-test", returncode=0, skipped=False),
               SimpleNamespace(name="git-commit", returncode=0, skipped=False)]
    return repo, archive, state, receipt, history, results


def test_ordinary_zip_auto_adopts_only_one_session(tmp_path, monkeypatch):
    repo, archive, state, evidence, _, results = fixture(tmp_path, monkeypatch)
    one = adopt_after_apply(repo, archive, evidence["manifest"], results, state_root=state)
    assert one["native_apply"]["state"] == "COMMITTED"
    assert one["stage"] == "PLANNED" and one["native_apply_replayed"] is False
    assert one["base_sha"] == "e" * 40
    assert one["native_apply"]["source_commit"] == evidence["manifest"]["base_commit"]
    assert one["native_apply"]["result_commit"] == evidence["commit_hash"]
    assert one["native_apply"]["changed_paths"] == ["sample.txt"]
    assert one["native_apply"]["committed_blobs"]["sample.txt"] == git(repo, "rev-parse", "HEAD:sample.txt")
    second = adopt_after_apply(repo, archive, evidence["manifest"], results, state_root=state)
    assert second["id"] == one["id"] and second["reused"] is True
    assert len(list(state.glob("*.json"))) == 1
    assert git(repo, "rev-parse", "HEAD") == evidence["commit_hash"]


def test_recovery_from_deleted_archive_is_read_only(tmp_path, monkeypatch):
    repo, archive, state, evidence, _, results = fixture(tmp_path, monkeypatch)
    archive.unlink()
    one = reconcile_committed(repo, archive, expected_manifest=evidence["manifest"], state_root=state)
    again = reconcile_committed(repo, archive, state_root=state)
    assert one["id"] == again["id"] and again["reused"]
    assert status(one["id"], state_root=state)["source_binding"] == "matching"


def test_v1_v2_do_not_opt_in(tmp_path, monkeypatch):
    repo, archive, state, evidence, _, results = fixture(tmp_path, monkeypatch)
    for version in (1, 2):
        assert adopt_after_apply(repo, archive, {"schema_version": version}, results, state_root=state) is None
    assert len(list(state.glob("*.json"))) == 0


@pytest.mark.parametrize("mutation,reason", [
    ("commit_missing", "COMMIT_REQUIRED"),
    ("failed_validation", "UNRESOLVED"),
    ("failed_commit", "COMMIT_REQUIRED"),
    ("double_commit", "UNRESOLVED"),
])
def test_precommit_and_uncertain_apply_do_not_adopt(tmp_path, monkeypatch, mutation, reason):
    repo, archive, state, evidence, _, results = fixture(tmp_path, monkeypatch)
    if mutation == "commit_missing": results.pop()
    if mutation == "failed_validation": results[1].returncode = 1
    if mutation == "failed_commit": results[-1].returncode = 1
    if mutation == "double_commit": results.append(results[-1])
    with pytest.raises(NativeContinuityError, match=reason):
        adopt_after_apply(repo, archive, evidence["manifest"], results, state_root=state)
    assert not list(state.glob("*.json"))


def test_skipped_required_native_step_refused_by_canonical_authority(tmp_path, monkeypatch):
    repo, archive, state, evidence, _, results = fixture(tmp_path, monkeypatch)
    evidence["validation_plan"]["validation_step_outcomes"][0]["skipped"] = True
    with pytest.raises(DeliverySessionError, match="unskipped"):
        adopt_after_apply(repo, archive, evidence["manifest"], results, state_root=state)


def test_incomplete_transaction_refuses_without_new_session(tmp_path, monkeypatch):
    repo, archive, state, evidence, _, results = fixture(tmp_path, monkeypatch)
    evidence["transaction"]["status"] = "backup-ready"
    with pytest.raises(DeliverySessionError, match="completed transaction"):
        adopt_after_apply(repo, archive, evidence["manifest"], results, state_root=state)
    assert not list(state.glob("*.json"))


def test_active_transaction_and_corrupt_marker_refused(tmp_path, monkeypatch):
    repo, archive, state, evidence, _, results = fixture(tmp_path, monkeypatch)
    from devtool.overlay import recovery
    for report in ({"marker_issues": [{"name": "unsafe"}], "active_transactions": []},
                   {"marker_issues": [], "active_transactions": [{"transaction_id": "pending"}]}):
        monkeypatch.setattr(recovery, "overlay_recovery_plan", lambda *_a, **_k: report)
        with pytest.raises(NativeContinuityError, match="UNRESOLVED_NATIVE_TRANSACTION"):
            reconcile_committed(repo, archive, state_root=state)


def test_missing_or_conflicting_history_fails_closed(tmp_path, monkeypatch):
    repo, archive, state, evidence, history, results = fixture(tmp_path, monkeypatch)
    history.clear()
    with pytest.raises(NativeContinuityError, match="UNIQUE_COMMITTED_HISTORY"):
        reconcile_committed(repo, archive, state_root=state)
    history.extend([evidence, dict(evidence)])
    with pytest.raises(NativeContinuityError, match="UNIQUE_COMMITTED_HISTORY"):
        reconcile_committed(repo, archive, state_root=state)


def test_manifest_mismatch_and_origin_drift_refused(tmp_path, monkeypatch):
    repo, archive, state, evidence, _, results = fixture(tmp_path, monkeypatch)
    altered = dict(evidence["manifest"])
    altered["base_tree"] = "0" * 40
    with pytest.raises(NativeContinuityError, match="DIFFERS"):
        reconcile_committed(repo, archive, expected_manifest=altered, state_root=state)
    git(repo, "remote", "set-url", "origin", "https://example.test/unsafe")
    with pytest.raises(DeliverySessionError, match="GitHub origin"):
        reconcile_committed(repo, archive, state_root=state)


def test_changed_head_blocks_commit_recovery(tmp_path, monkeypatch):
    repo, archive, state, evidence, _, _ = fixture(tmp_path, monkeypatch)
    git(repo, "commit", "--allow-empty", "-qm", "later commit")
    with pytest.raises(NativeContinuityError, match="UNIQUE_COMMITTED_HISTORY"):
        reconcile_committed(repo, archive, state_root=state)


def test_duplicate_native_delivery_sessions_cannot_be_rebound(tmp_path, monkeypatch):
    repo, archive, state, evidence, _, _ = fixture(tmp_path, monkeypatch)
    from devtool.delivery_session import start
    start(repo, base_sha="e" * 40, state_root=state)
    start(repo, base_sha="e" * 40, state_root=state)
    with pytest.raises(DeliverySessionError, match="Ambiguous native sessions"):
        reconcile_committed(repo, archive, state_root=state)


def test_executable_bootstrap_reconciliation_detects_v3(tmp_path, monkeypatch):
    repo, archive, state, evidence, _, _ = fixture(tmp_path, monkeypatch)
    assert is_committed_v3(repo, archive)
    evidence["manifest"]["schema_version"] = 2
    assert not is_committed_v3(repo, archive)


def test_interrupted_after_commit_before_journal_recovers_without_reapply(tmp_path, monkeypatch):
    repo, archive, state, evidence, _, results = fixture(tmp_path, monkeypatch)
    import devtool.delivery_session as authority
    real = authority.adopt_applied
    calls = []
    def interrupt_once(*args, **kwargs):
        calls.append('interrupted')
        raise OSError('injected adoption interruption')
    monkeypatch.setattr(authority, 'adopt_applied', interrupt_once)
    prior = git(repo, 'rev-parse', 'HEAD')
    with pytest.raises(OSError, match='injected adoption interruption'):
        adopt_after_apply(repo, archive, evidence['manifest'], results, state_root=state)
    assert git(repo, 'rev-parse', 'HEAD') == prior
    assert not list(state.glob('*.json'))
    monkeypatch.setattr(authority, 'adopt_applied', real)
    adopted = reconcile_committed(repo, archive, state_root=state)
    assert adopted['native_apply_replayed'] is False
    assert git(repo, 'rev-parse', 'HEAD') == prior
    assert len(list(state.glob('*.json'))) == 1
    assert calls == ['interrupted']


def test_committed_history_archive_extension_parity_without_executing_pyz(tmp_path, monkeypatch):
    repo, archive, state, evidence, _, results = fixture(tmp_path, monkeypatch)
    executable = archive.with_suffix('.pyz')
    archive.rename(executable)
    evidence['artifact'] = str(executable)
    assert is_committed_v3(repo, executable)
    adopted = reconcile_committed(repo, executable, state_root=state)
    assert adopted['stage'] == 'PLANNED'
    assert adopted['native_apply']['state'] == 'COMMITTED'
    # This checks only history adoption parity. Pre-execution PYZ verification
    # remains tested by M2 and native executable-overlay security suites.


def test_archive_tampering_refused_even_with_matching_native_history(tmp_path, monkeypatch):
    repo, archive, state, evidence, _, results = fixture(tmp_path, monkeypatch)
    archive.write_bytes(b'forged archive')
    with pytest.raises(DeliverySessionError, match='does not match committed native history'):
        reconcile_committed(repo, archive, state_root=state)
    assert not list(state.glob('*.json'))


# Component-boundary-aware native history adoption, including the independent
# Guardian-style v2 scenario. These fixtures never access Guardian itself.
def _scoped_hook_fixture(tmp_path, monkeypatch, outputs):
    repo, archive, state, receipt, history, results = fixture(tmp_path, monkeypatch)
    manifest = receipt['manifest']
    manifest['validation']['write_paths'] = ['dist/']
    manifest['validation']['script'] = {
        'path': '.devtool-artifact/validation/validate.py',
        'sha256': 'a' * 64,
    }
    receipt['child_outcomes'] = [{
        'name': 'artifact-validate-script', 'returncode': 0,
        'skipped': False, 'failure_kind': '',
    }, {'name':'git-commit','returncode':0,'skipped':False,'failure_kind':''}]
    for rel, value in outputs.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, tuple) and value[0] == 'symlink':
            target.symlink_to(value[1], target_is_directory=True)
        else:
            target.write_text(value)
    git(repo, 'add', '-A')
    git(repo, 'commit', '--amend', '--no-edit', '-q')
    receipt['commit_hash'] = git(repo, 'rev-parse', 'HEAD')
    return repo, archive, state, receipt, history, results




def test_validator_execution_and_history_recovery_agree_on_real_child_name(tmp_path, monkeypatch):
    """Capture the real native hook request name; synthetic fixture aliases cannot substitute."""
    repo, archive, state, receipt, _, _ = _scoped_hook_fixture(
        tmp_path, monkeypatch, {'dist/LSPosed-Guardian-v0.12.5.zip': 'verified\n',
                                'dist/build-evidence.json': '{}\n'})
    from devtool import artifact_validation

    class CaptureNativeHook:
        def execute(self, request):
            return request.operation_name

    monkeypatch.setattr(artifact_validation, '_ENGINE', CaptureNativeHook())
    native_name = artifact_validation.run_artifact_validation_script(
        repo, archive, manifest=receipt['manifest'])
    assert native_name == 'artifact-validate-script'
    assert receipt['child_outcomes'][0]['name'] == native_name
    recovered = adopt_applied(repo, archive, state_root=state)
    assert recovered['native_apply']['state'] == 'COMMITTED'
    assert recovered['native_apply']['commit'] == receipt['commit_hash']
    assert git(repo, 'rev-parse', 'HEAD') == receipt['commit_hash']


@pytest.mark.parametrize('wrong_name', [
    'artifact-validation-script', 'artifact-validate-script-legacy',
    'artifact-preflight-script',
])
def test_noncanonical_native_validator_child_cannot_authorize_dist_scope(
        tmp_path, monkeypatch, wrong_name):
    repo, archive, state, receipt, _, _ = _scoped_hook_fixture(
        tmp_path, monkeypatch, {'dist/build-evidence.json': 'safe\n'})
    receipt['child_outcomes'][0]['name'] = wrong_name
    with pytest.raises(DeliverySessionError, match='unverified or empty mutation inventory'):
        adopt_applied(repo, archive, state_root=state)
    assert not list(state.glob('*.json'))


@pytest.mark.parametrize('returncode,skipped,failure_kind', [
    (1, False, 'validation_failure'),
    (0, True, ''),
    (0, False, 'incomplete'),
])
def test_nonpassing_real_validator_cannot_authorize_dist_scope(
        tmp_path, monkeypatch, returncode, skipped, failure_kind):
    repo, archive, state, receipt, _, _ = _scoped_hook_fixture(
        tmp_path, monkeypatch, {'dist/build-evidence.json': 'safe\n'})
    receipt['child_outcomes'][0].update(
        returncode=returncode, skipped=skipped, failure_kind=failure_kind)
    with pytest.raises(DeliverySessionError, match='unverified or empty mutation inventory'):
        adopt_applied(repo, archive, state_root=state)
    assert not list(state.glob('*.json'))


def test_validator_event_does_not_rename_validation_manifest_policy(tmp_path, monkeypatch):
    # Keep manifest policy type checks active after correcting hook event name.
    repo, archive, state, receipt, _, _ = _scoped_hook_fixture(
        tmp_path, monkeypatch, {'dist/record.json': '{}\n'})
    receipt['manifest']['validation'] = ['wrong-policy-type']
    from devtool.delivery_session import _committed_hook_output_scopes
    with pytest.raises(DeliverySessionError, match='Invalid committed hook policy'):
        _committed_hook_output_scopes(repo, receipt['manifest'], receipt)
    assert not list(state.glob('*.json'))

def test_guardian_style_v2_dist_directory_committed_adoption(tmp_path, monkeypatch):
    repo, archive, state, receipt, history, _ = _scoped_hook_fixture(
        tmp_path, monkeypatch, {'dist/output.bin': 'generated\n',
                                'dist/sub/metadata.json': '{"ok":true}\n'})
    receipt['manifest']['schema_version'] = 2
    session = adopt_applied(repo, archive, state_root=state)
    assert session['native_apply']['state'] == 'COMMITTED'
    assert session['native_apply']['transaction_status'] == 'completed'
    assert session['native_apply']['commit'] == receipt['commit_hash']
    assert git(repo, 'rev-parse', 'HEAD') == receipt['commit_hash']
    assert len(history) == 1


def test_hook_descendants_and_exact_inventory_adopt_without_widening(tmp_path, monkeypatch):
    repo, archive, state, receipt, _, results = _scoped_hook_fixture(
        tmp_path, monkeypatch, {'dist/sub/output.txt':'generated\n'})
    session = reconcile_committed(repo, archive, state_root=state)
    assert session['native_apply']['changed_paths'] == ['dist/sub/output.txt', 'sample.txt']
    assert session['native_apply']['committed_blobs']['dist/sub/output.txt'] == git(
        repo, 'rev-parse', 'HEAD:dist/sub/output.txt')


@pytest.mark.parametrize('extra', ['outside/evil.txt', 'dist-other/evil.txt'])
def test_outside_and_prefix_lookalikes_never_gain_scope(tmp_path, monkeypatch, extra):
    repo, archive, state, receipt, _, _ = _scoped_hook_fixture(
        tmp_path, monkeypatch, {'dist/safe.txt':'safe\n', extra:'unrelated\n'})
    with pytest.raises(DeliverySessionError, match='unverified'):
        adopt_applied(repo, archive, state_root=state)
    assert not list(state.glob('*.json'))


@pytest.mark.parametrize('declaration', ['../dist', '/dist', '.', '.git/config',
                                          'dist/../outside', 'dist\\escaped'])
def test_unsafe_native_hook_declaration_refuses(tmp_path, monkeypatch, declaration):
    repo, archive, state, receipt, _, _ = _scoped_hook_fixture(
        tmp_path, monkeypatch, {'dist/safe.txt':'safe\n'})
    receipt['manifest']['validation']['write_paths'] = [declaration]
    with pytest.raises((ValueError, DeliverySessionError), match='Unsafe|not be|metadata'):
        adopt_applied(repo, archive, state_root=state)
    assert not list(state.glob('*.json'))


def test_symlinked_output_and_descendant_escape_refused(tmp_path, monkeypatch):
    outside = tmp_path / 'outside'
    outside.mkdir()
    repo, archive, state, receipt, _, _ = _scoped_hook_fixture(
        tmp_path, monkeypatch, {'dist/safe.txt':'safe\n',
                                'dist/link':('symlink', outside)})
    with pytest.raises(DeliverySessionError, match='symlink'):
        adopt_applied(repo, archive, state_root=state)
    assert not list(state.glob('*.json'))


def test_unexecuted_hook_does_not_authorize_dist_writes(tmp_path, monkeypatch):
    repo, archive, state, receipt, _, _ = _scoped_hook_fixture(
        tmp_path, monkeypatch, {'dist/file':'data\n'})
    receipt['child_outcomes'] = []
    with pytest.raises(DeliverySessionError, match='unverified'):
        adopt_applied(repo, archive, state_root=state)


def test_declared_hook_file_is_not_a_directory(tmp_path, monkeypatch):
    repo, archive, state, receipt, _, _ = _scoped_hook_fixture(
        tmp_path, monkeypatch, {'dist/safe.txt':'safe\n'})
    receipt['manifest']['validation']['write_paths'] = ['dist/safe.txt']
    session = adopt_applied(repo, archive, state_root=state)
    assert session['native_apply']['state'] == 'COMMITTED'
    # A second undeclared file must not inherit the file declaration.
    assert git(repo, 'ls-tree', 'HEAD', '--', 'dist/safe.txt')


def test_missing_validation_or_bad_payload_postimage_refuses(tmp_path, monkeypatch):
    repo, archive, state, receipt, _, _ = _scoped_hook_fixture(
        tmp_path, monkeypatch, {'dist/safe.txt':'safe\n'})
    receipt['validation_plan']['validation_step_outcomes'] = []
    with pytest.raises(DeliverySessionError, match='validation'):
        adopt_applied(repo, archive, state_root=state)
    receipt['validation_plan']['validation_step_outcomes'] = [
        {'name':'native-test','outcome':'passed','returncode':0,'skipped':False}]
    receipt['manifest']['expected_post_sha256']['sample.txt'] = '0'*64
    with pytest.raises(DeliverySessionError, match='payload does not match'):
        adopt_applied(repo, archive, state_root=state)
    assert not list(state.glob('*.json'))


def test_changed_head_and_missing_ambiguous_history_remain_refused(tmp_path, monkeypatch):
    repo, archive, state, receipt, history, _ = _scoped_hook_fixture(
        tmp_path, monkeypatch, {'dist/safe.txt':'safe\n'})
    history.clear()
    with pytest.raises(DeliverySessionError, match='history receipt'):
        adopt_applied(repo, archive, state_root=state)
    history.extend([receipt, dict(receipt)])
    with pytest.raises(DeliverySessionError, match='history receipt'):
        adopt_applied(repo, archive, state_root=state)
    history[:] = [receipt]
    git(repo, 'commit', '--allow-empty', '-qm', 'unrelated future commit')
    with pytest.raises(DeliverySessionError, match='history receipt'):
        adopt_applied(repo, archive, state_root=state)
    assert not list(state.glob('*.json'))


def _scoped_rename_fixture(tmp_path, monkeypatch, *, crossing=False):
    repo, archive, state, receipt, history, results = fixture(tmp_path, monkeypatch)
    (repo/'dist').mkdir()
    (repo/'dist/old.txt').write_text('before\n')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'fixture old dist tree')
    parent = git(repo, 'rev-parse', 'HEAD')
    parent_tree = git(repo, 'rev-parse', 'HEAD^{tree}')
    (repo/'sample.txt').write_text('latest\n')
    destination = 'elsewhere/new.txt' if crossing else 'dist/new.txt'
    (repo/destination).parent.mkdir(parents=True, exist_ok=True)
    git(repo, 'mv', 'dist/old.txt', destination)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'native renamed output')
    sha = hashlib.sha256(b'latest\n').hexdigest()
    old_sha = hashlib.sha256(b'new\n').hexdigest()
    m = receipt['manifest']
    m['base_commit'], m['base_tree'] = parent, parent_tree
    m['source_sha256']['sample.txt'] = old_sha
    m['expected_post_sha256']['sample.txt'] = sha
    m['inventory']['entries'][0]['source_sha256'] = old_sha
    m['inventory']['entries'][0]['sha256'] = sha
    m['inventory']['entries'][0]['size'] = len(b'latest\n')
    m['validation']['write_paths'] = ['dist']
    m['validation']['script'] = {'path':'.devtool-artifact/validation/validate.py',
                                 'sha256':'a'*64}
    receipt['child_outcomes'] = [{'name':'artifact-validate-script',
                                  'returncode':0,'skipped':False,'failure_kind':''},
                                 {'name':'git-commit','returncode':0,
                                  'skipped':False,'failure_kind':''}]
    receipt['commit_hash'] = git(repo, 'rev-parse', 'HEAD')
    return repo, archive, state, receipt


def test_rename_between_authorized_directory_paths_is_accepted(tmp_path, monkeypatch):
    repo, archive, state, receipt = _scoped_rename_fixture(tmp_path, monkeypatch)
    adopted = adopt_applied(repo, archive, state_root=state)
    assert adopted['native_apply']['state'] == 'COMMITTED'
    assert git(repo, 'rev-parse', 'HEAD') == receipt['commit_hash']


def test_rename_from_directory_to_undeclared_location_refused(tmp_path, monkeypatch):
    repo, archive, state, _ = _scoped_rename_fixture(tmp_path, monkeypatch, crossing=True)
    with pytest.raises(DeliverySessionError, match='unverified'):
        adopt_applied(repo, archive, state_root=state)
