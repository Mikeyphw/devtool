"""R06B read-only adoption/overlay plans; never grant remote or release authority."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from devtool.actions_adoption import AdoptionError, adoption_doctor
from devtool.actions_cli import run_actions_command
from devtool.overlay.planning import overlay_plan


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], text=True, capture_output=True, check=True).stdout.strip()


def repo(tmp_path):
    root = tmp_path / 'consumer'
    root.mkdir()
    (root / '.devtool.toml').write_text('''[project]
runner = "python"
[workspace]
default_target = "app"
[wrapper]
schema = 2
[targets.app]
runner = "python"
root = "."
[targets.app.jobs.unit]
runner = "command"
command = ["python3", "-m", "pytest"]
[targets.app.workflows]
ci = [{id="unit", ref="job:unit"}]
[targets.bad]
runner = "python"
root = "."
[targets.bad.jobs.device]
runner = "command"
command = ["adb", "shell", "true"]
[targets.bad.workflows]
ci = [{id="device", ref="job:device"}]
''')
    w = root / 'devtoolw'; w.write_text('#!/bin/sh\nexit 0\n'); w.chmod(0o755)
    git(root, 'init', '-q')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=t@example.org', 'add', '.')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=t@example.org', 'commit', '-qm', 'source')
    return root


def test_doctor_review_only_and_existing_a09_authority(tmp_path):
    root = repo(tmp_path)
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    payload = adoption_doctor(root)
    by = {row['target']: row for row in payload['workflows']}
    assert by['app']['status'] == 'locally_eligible'
    assert by['bad']['status'] == 'blocked' and 'adb' in by['bad']['blockers'][0]
    assert payload['counts'] == {'locally_eligible': 1, 'review_required': 0, 'blocked': 1}
    assert payload['read_only'] and payload['qualification'] == 'LOCAL_REVIEW_ONLY'
    assert not payload['hosted_qualified'] and not payload['device_qualified']
    assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_doctor_dirty_and_conflicting_destination_are_review_only(tmp_path):
    root = repo(tmp_path)
    (root / '.github/workflows').mkdir(parents=True)
    (root / '.github/workflows/devtool-app-ci.yml').write_text('owned by somebody else')
    payload = adoption_doctor(root, target='app')
    assert payload['workflows'][0]['status'] == 'review_required'
    assert payload['workflows'][0]['blockers']
    assert (root / '.github/workflows/devtool-app-ci.yml').read_text() == 'owned by somebody else'
    with pytest.raises(AdoptionError):
        adoption_doctor(root, target='../bad')


def test_doctor_symlink_protection(tmp_path):
    root = repo(tmp_path)
    (root / '.github').symlink_to(tmp_path, target_is_directory=True)
    payload = adoption_doctor(root, target='app')
    assert payload['workflows'][0]['status'] == 'review_required'
    assert any('symlink' in b.lower() for b in payload['workflows'][0]['blockers'])


def test_doctor_cli_json_is_read_only(tmp_path, capsys):
    root = repo(tmp_path)
    assert run_actions_command(SimpleNamespace(actions_command='adoption-doctor', repo=str(root), adopt_target='app', json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['counts']['locally_eligible'] == 1
    assert git(root, 'status', '--porcelain') == ''


def test_overlay_plan_composes_existing_security_contract(monkeypatch, tmp_path):
    import devtool.overlay.planning as planner
    from types import SimpleNamespace
    root = repo(tmp_path)
    def outcome(struct=True, source=True, conflicts=()):
        return SimpleNamespace(structural_ok=struct, source_state_ok=source, conflicts=conflicts, messages=())
    statuses = {'value': 'absent'}
    monkeypatch.setattr(planner, 'check_overlay', lambda *_: outcome())
    monkeypatch.setattr(planner, 'inspect_patch_equivalence', lambda *_: SimpleNamespace(to_dict=lambda: {'status': statuses['value']}))
    candidate = overlay_plan(root, 'unused.zip')
    assert candidate['decision'] == 'candidate_only'
    assert candidate['read_only'] and not candidate['safe_to_auto_apply']
    statuses['value'] = 'fully-present'
    assert overlay_plan(root, 'unused.zip')['decision'] == 'already_present'
    statuses['value'] = 'partially-present'
    assert overlay_plan(root, 'unused.zip')['decision'] == 'manual_review'
    monkeypatch.setattr(planner, 'check_overlay', lambda *_: outcome(False,False))
    assert overlay_plan(root, 'unused.zip')['decision'] == 'refuse'
    assert git(root, 'status', '--porcelain') == ''


def test_overlay_plan_dirty_source_and_unknown_patch_refuse_auto_apply(monkeypatch, tmp_path):
    import devtool.overlay.planning as planner
    from types import SimpleNamespace
    root = repo(tmp_path)
    (root / 'untracked').write_text('mine')
    monkeypatch.setattr(planner, 'check_overlay', lambda *_: SimpleNamespace(structural_ok=True,source_state_ok=True,conflicts=(),messages=[]))
    monkeypatch.setattr(planner, 'inspect_patch_equivalence', lambda *_: SimpleNamespace(to_dict=lambda: {'status': 'absent'}))
    assert overlay_plan(root, 'unused.zip')['decision'] == 'manual_review'
    monkeypatch.setattr(planner, 'inspect_patch_equivalence', lambda *_: (_ for _ in ()).throw(RuntimeError('fail')))
    assert overlay_plan(root, 'unused.zip')['decision'] == 'manual_review'
    assert (root / 'untracked').read_text() == 'mine'


def test_cli_identity_and_generated_r06b_commands():
    from devtool.cli import build_parser
    from devtool.core_registry import build_core_registry
    p = build_parser()
    assert p.parse_args(['actions','adoption-doctor','--target','app','--json']).actions_command == 'adoption-doctor'
    assert p.parse_args(['overlay','plan','/tmp/a.zip','--json']).overlay_command == 'plan'
    assert build_core_registry().parser_parity(p)['missing_count'] == 0


def test_existing_added_file_checks_real_hash_without_nameerror(tmp_path):
    from devtool.overlay.conflicts import detect_inventory_conflicts
    root = tmp_path / 'repo'; root.mkdir()
    p=root/'added.txt'; p.write_text('already here')
    manifest={'apply': {'conflict_policy': 'fail'},'inventory': {'format':1, 'entries':[{'path':'added.txt','action':'add'}]}}
    found=detect_inventory_conflicts(root,manifest)
    assert len(found)==1
    assert found[0].kind == 'add-existing'
    assert len(found[0].actual) == 64
    assert p.read_text() == 'already here'
    target = tmp_path/'other';target.write_text('link target')
    p.unlink();p.symlink_to(target)
    found=detect_inventory_conflicts(root,manifest)
    assert len(found)==1 and found[0].rebaseable is False


def test_legacy_exact_hash_presence_and_preimage_fallback(tmp_path):
    from devtool.overlay.planning import _legacy_inventory_presence
    import hashlib
    root=tmp_path/'legacy';root.mkdir()
    sha=lambda b: hashlib.sha256(b).hexdigest()
    (root/'mod').write_bytes(b'new')
    (root/'added').write_bytes(b'added')
    inventory={'inventory':{'format':1,'entries':[
        {'action':'modify','path':'mod','sha256':sha(b'new'),'source_sha256':sha(b'old')},
        {'action':'add','path':'added','sha256':sha(b'added')},
    ]}}
    got=_legacy_inventory_presence(root,inventory)
    assert got['status']=='fully-present' and got['not_git_patch_equivalence']
    assert got['authority']=='bounded-legacy-inventory-hashes'
    (root/'mod').write_bytes(b'old')
    (root/'added').unlink()
    assert _legacy_inventory_presence(root,inventory)['status']=='absent'
    (root/'mod').write_bytes(b'new')
    assert _legacy_inventory_presence(root,inventory)['status']=='ambiguous'
    (root/'added').symlink_to(root/'mod')
    assert _legacy_inventory_presence(root,inventory)['status']=='ambiguous'


def test_existing_real_legacy_zip_does_not_require_reinstall(tmp_path):
    # The caller still requires validated archive structure. This fixture
    # exercises only deterministic hash-based classification, not trust.
    from devtool.overlay.planning import _legacy_inventory_presence
    import hashlib
    root = tmp_path/'repo';root.mkdir()
    content=b'done';(root/'one').write_bytes(content)
    assert _legacy_inventory_presence(root,{'inventory':{'format':1,'entries':[
      {'action':'add','path':'one','sha256':hashlib.sha256(content).hexdigest()}
    ]}})['status']=='fully-present'
