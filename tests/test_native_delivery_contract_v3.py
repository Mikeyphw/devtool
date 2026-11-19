"""M1 data-only schema contract and M0 canonical required-test qualification.

No GitHub writes, overlay apply, remote commands, device, release or CI.
"""
from __future__ import annotations

from copy import deepcopy
import pytest

from devtool.native_delivery_contract_v3 import (
    NativeDeliveryContractError, inspect_delivery_contract, normalize_delivery_contract,
)
from devtool.artifacts import _schema_version
from devtool.artifact_lifecycle import _has_required_inline_validation_tests

SHA0 = 'a' * 40
SHA1 = 'b' * 40
BASE = 'c' * 40
SHA256_A = 'd' * 64
SHA256_B = 'e' * 64


def valid() -> dict:
    return {
        'schema_version': 3,
        'type': 'overlay',
        'base_contract_mode': 'exact-tree',
        'base_commit': SHA0,
        'base_tree': SHA1,
        'repository': {
            'remote': 'git@github.com:Mikeyphw/devtool.git',
            'branch': 'feature/source-bound-delivery',
            'enforce_remote': True,
            'enforce_branch': True,
        },
        'inventory': {'format': 1, 'entries': [
            {'path': 'docs/new.md', 'action': 'add', 'sha256': SHA256_A, 'size': 10},
            {'path': 'devtool/old.py', 'action': 'modify',
             'source_sha256': SHA256_A, 'sha256': SHA256_B, 'size': 15},
        ]},
        'touched_paths': ['devtool/old.py', 'docs/new.md'],
        'source_sha256': {'devtool/old.py': SHA256_A, 'docs/new.md': None},
        'expected_post_sha256': {'docs/new.md': SHA256_A, 'devtool/old.py': SHA256_B},
        'validation': {'required': True, 'restore': False, 'build': False, 'test': False,
                       'tests': [{'id': 'native-regression', 'runner': 'python',
                                  'required': True, 'command': ['python3', '-B', '-m', 'pytest', '-q']}]},
        'apply': {'atomic': True, 'requires_clean_worktree': True,
                  'commit': {'enabled': True, 'strategy': 'single', 'message': 'feat: source-bound delivery'}},
        'delivery_contract': {
            'version': 1,
            'runtime': {'compatibility': {'minimum': '3.2.0', 'major': 3},
                        'resolution': 'trusted-verified-compatible',
                        'verify': ['commit', 'tree', 'launcher-module-blob', 'cli-schema-blob']},
            'session': {'after_commit': 'adopt-or-resume',
                        'bind_to': ['artifact-digest', 'transaction-id', 'result-commit', 'branch']},
            'remote': {'base_repository': 'Mikeyphw/devtool', 'base_branch': 'main',
                       'base_sha': BASE, 'push': 'requires-explicit-approval',
                       'pull_request': {'mode': 'requires-explicit-approval',
                                        'draft': True, 'title': 'feat: source-bound delivery'}},
            'ci': {'policy_path': 'config/delivery-required-ci.json',
                   'observe': 'on-explicit-request', 'exact_pr_head': True,
                   'qualification': 'required-workflows-only'},
            'recovery': {'after_commit': 'resume-only', 'unknown_remote_outcome': 'read-only-reconcile'},
        },
    }


def test_valid_v3_is_declaration_only_not_a_pass():
    d = normalize_delivery_contract(valid())
    assert d['status'] == 'declared-not-executed'
    assert d['runtime']['verified'] is False
    assert d['session']['adopted'] is False
    assert d['remote']['approved'] is False
    assert d['ci']['state'] == 'NOT_QUALIFIED'
    assert d['source']['inventory_paths'] == ['devtool/old.py', 'docs/new.md']


def test_native_inspect_never_forges_qualification():
    good = inspect_delivery_contract(valid())
    assert good['runtime']['verified'] is False
    bad = valid()
    bad['delivery_contract']['ci']['exact_pr_head'] = False
    view = inspect_delivery_contract(bad)
    assert view['status'] == 'REFUSED'
    assert 'exact' in view['reason']
    assert view['ci_state'] == 'NOT_QUALIFIED'


def test_legacy_v1_v2_remain_opt_in():
    for schema in (1, 2):
        assert normalize_delivery_contract({'schema_version': schema}) is None
        errors = []
        assert _schema_version({'schema_version': schema}, errors) == schema
        assert not errors


def test_v3_artifact_version_not_bundle():
    errors = []
    assert _schema_version({'schema_version': 3}, errors) == 3
    assert not errors
    errors = []
    assert _schema_version({'schema_version': 3}, errors, bundle=True) is None
    assert any('maximum supported version is 2' in x for x in errors)


def test_legacy_cannot_silently_ignore_new_delivery_fields():
    for schema in (1, 2):
        with pytest.raises(NativeDeliveryContractError, match='requires schema_version=3'):
            normalize_delivery_contract({'schema_version': schema, 'delivery_contract': {}})


def test_missing_v3_contract_fails_closed():
    d=valid()
    d.pop('delivery_contract')
    with pytest.raises(NativeDeliveryContractError, match='requires delivery_contract'):
        normalize_delivery_contract(d)


@pytest.mark.parametrize('path,value,reason', [
    (('delivery_contract','extra'), 'new-capability', 'unknown and missing'),
    (('delivery_contract','version'), 2, 'version'),
    (('delivery_contract','runtime','compatibility','minimum'), '3.1.9', '3.x'),
    (('delivery_contract','runtime','compatibility','minimum'), '4.0.0', '3.x'),
    (('delivery_contract','runtime','compatibility','major'), 4, '3.x'),
    (('delivery_contract','runtime','resolution'), 'execute-pyz-first', 'resolver'),
    (('delivery_contract','runtime','verify'), ['commit'], 'runtime.verify'),
    (('delivery_contract','session','after_commit'), 'reapply', 'session adoption'),
    (('delivery_contract','session','bind_to'), ['transaction-id'], 'commit identity'),
    (('delivery_contract','remote','push'), 'auto', 'implicit mutation'),
    (('delivery_contract','remote','pull_request','mode'), 'auto', 'explicitly approved'),
    (('delivery_contract','remote','pull_request','draft'), False, 'draft'),
    (('delivery_contract','remote','base_sha'), '0123', 'SHA-1'),
    (('delivery_contract','remote','base_branch'), '../main', 'safe named Git branch'),
    (('delivery_contract','ci','policy_path'), '../delivery-required-ci.json', 'CI policy'),
    (('delivery_contract','ci','observe'), 'auto', 'CI policy'),
    (('delivery_contract','ci','exact_pr_head'), False, 'exact PR-head'),
    (('delivery_contract','ci','qualification'), 'any-green', 'qualification'),
    (('delivery_contract','recovery','after_commit'), 'rerun-apply', 'resume-only'),
    (('delivery_contract','recovery','unknown_remote_outcome'), 'retry-post', 'read-only'),
    (('base_commit',), 'a'*39, 'SHA-1'),
    (('base_tree',), 'abc', 'SHA-1'),
    (('base_contract_mode',), 'touched-files', 'exact-tree'),
    (('repository','remote'), 'https://evil.test/Mikeyphw/devtool', 'canonical GitHub'),
    (('repository','branch'), 'main', 'own PR base branch'),
    (('repository','enforce_remote'), False, 'enforced remote'),
    (('repository','enforce_branch'), False, 'enforced remote'),
    (('touched_paths',), [], 'touched_paths'),
    (('expected_post_sha256','docs/new.md'), 'bad', 'expected_post_sha256'),
    (('validation','required'), False, 'validation.required'),
    (('validation','static_only'), True, 'validation.required'),
    (('apply','atomic'), False, 'apply.atomic'),
    (('apply','requires_clean_worktree'), False, 'apply.atomic'),
    (('apply','commit','enabled'), False, 'apply.commit'),
    (('apply','commit','strategy'), 'merge', 'apply.commit'),
])
def test_adversarial_contract_fields(path, value, reason):
    d = deepcopy(valid())
    node = d
    for item in path[:-1]:
        node = node[item]
    node[path[-1]] = value
    with pytest.raises(NativeDeliveryContractError, match=reason):
        normalize_delivery_contract(d)


@pytest.mark.parametrize('name', ['../secret', '/absolute', 'a\\b', 'folder//alias', '.git/config', 'foo/./bar'])
def test_traversal_alias_and_reserved_paths(name):
    d = valid()
    d['inventory']['entries'][0]['path'] = name
    with pytest.raises(NativeDeliveryContractError, match='path|touched_paths'):
        normalize_delivery_contract(d)


def test_duplicate_inventory_disallowed():
    d=valid()
    d['inventory']['entries'].append(deepcopy(d['inventory']['entries'][0]))
    with pytest.raises(NativeDeliveryContractError, match='duplicate inventory'):
        normalize_delivery_contract(d)


def test_optional_only_tests_do_not_qualify_required_validation():
    d=valid()
    d['validation']['tests'][0]['required'] = False
    with pytest.raises(NativeDeliveryContractError, match='no executable'):
        normalize_delivery_contract(d)


def test_duplicate_root_delivery_or_commit_refused():
    for key in ('delivery', 'delivery_v3', 'commit', 'git', 'commit_message'):
        d=valid()
        d[key]={}
        with pytest.raises(NativeDeliveryContractError, match='duplicate'):
            normalize_delivery_contract(d)


def test_fork_base_identity_is_distinct_from_target_preimage():
    d=valid()
    d['delivery_contract']['remote']['base_repository']='upstream/devtool'
    d['delivery_contract']['remote']['base_sha']='f'*40
    view=normalize_delivery_contract(d)
    assert view['remote']['base_repository']=='upstream/devtool'
    assert view['remote']['base_sha'] != view['source']['base_commit']


def test_m0_inline_required_command_is_native_executable():
    d=valid()
    assert _has_required_inline_validation_tests(d)
    d['validation']['tests'][0]['required'] = False
    assert not _has_required_inline_validation_tests(d)
    d['validation']['tests'][0]['required'] = True
    d['validation']['tests'][0]['command'] = []
    assert not _has_required_inline_validation_tests(d)


def test_negative_unknown_nested_required_capability():
    d=valid()
    d['delivery_contract']['runtime']['unknown_required_capability']='unsafe'
    with pytest.raises(NativeDeliveryContractError, match='unknown and missing'):
        normalize_delivery_contract(d)


def test_m0_quoted_command_uses_canonical_native_normalization():
    d = valid()
    d['validation']['tests'][0]['command'] = 'python3 -B -m pytest -q'
    assert _has_required_inline_validation_tests(d)


def test_m0_native_gradle_task_is_executable_but_never_implicitly_invoked():
    d = valid()
    d['validation']['tests'][0] = {'id': 'gradle-task', 'runner': 'gradle',
                                   'task': 'testDebugUnitTest', 'required': True}
    assert _has_required_inline_validation_tests(d)
    assert normalize_delivery_contract(d)['status'] == 'declared-not-executed'
