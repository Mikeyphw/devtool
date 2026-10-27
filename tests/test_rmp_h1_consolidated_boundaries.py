from __future__ import annotations

import json
import pytest

from devtool.environment_composition import _safe_env_identity
from devtool.models import CommandSpec
from devtool.validation_sessions import ValidationSessionError, serialize_spec
from devtool.test_runtime_optimization import ShardOutcome, aggregate_shards, run_attachment_manifest
from devtool.presentation_surfaces import status_from_exo, PresentationSurface


def test_c06_secret_rotation_not_in_stable_composition_identity():
    a = _safe_env_identity({'PATH':'/bin', 'API_TOKEN':'candidate-one', 'PASSWORD':'p@ss-1'})
    b = _safe_env_identity({'PATH':'/bin', 'API_TOKEN':'candidate-two', 'PASSWORD':'p@ss-2'})
    assert a == b
    assert 'candidate-one' not in json.dumps(a)
    assert 'sha256:' not in json.dumps(a)
    assert a != _safe_env_identity({'PATH':'/usr/bin', 'API_TOKEN':'candidate-two', 'PASSWORD':'p@ss-2'})


@pytest.mark.parametrize('metadata', [
    {'api_key': 'not-an-env-var'},
    {'credentials': {'password': 'untracked-credential'}},
    {'session_token': ['inline-first', 'inline-second']},
    {'authorization': 'Bearer untracked'},
    {'nested': {'secret': 12345}},
])
def test_n02_nested_untracked_metadata_credential_fails_closed(metadata):
    with pytest.raises(ValidationSessionError, match='contains a credential'):
        serialize_spec(CommandSpec('safe', ['true'], metadata=metadata))


def test_n02_reference_metadata_remains_resumable():
    payload = serialize_spec(CommandSpec('safe', ['true'], metadata={
        'reuse_secret_identities': ['vault:key-1'], 'session_token': '@env:SESSION_TOKEN',
        'path': '/tmp/fixture',
    }))
    assert payload['metadata']['session_token'] == '@env:SESSION_TOKEN'


def test_c04_shard_manifest_binds_cases_and_terminal_counts():
    agg = aggregate_shards(('a','b'),(ShardOutcome('s1',('a',),passed=1),ShardOutcome('s2',('b',),skipped=1)))
    assert run_attachment_manifest('run','selection',agg)['complete']
    altered = dict(agg)
    for attachments in ([{'shard_id':'s1'},{'shard_id':'s2'}],
                        [{**agg['attachments'][0], 'case_ids':['a','a']},agg['attachments'][1]],
                        [{**agg['attachments'][0], 'passed':2},agg['attachments'][1]],
                        []):
        altered['attachments']=attachments
        assert not run_attachment_manifest('run','selection',altered)['complete']
    altered = dict(agg, passed='1', skipped=1)
    assert not run_attachment_manifest('run','selection',altered)['complete']


@pytest.mark.parametrize('surface', list(PresentationSurface))
def test_c05_failure_outside_visible_window_never_projects_success(surface):
    records=[{'id':str(i),'status':'passed'} for i in range(60)]+[{'id':'error','status':'failed'}]
    result=status_from_exo(records,surface=surface,limit=15)
    assert result.truncated
    assert len(result.events)==15
    assert result.status=='failed'


def test_c05_complete_pass_remains_success():
    assert status_from_exo([{'status':'passed'} for _ in range(60)],limit=15).status=='passed'
