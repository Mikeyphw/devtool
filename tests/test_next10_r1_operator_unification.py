"""NEXT-10-R1 regression and refusal tests: no network, GH writes, Gradle, or device."""
from pathlib import Path
import json
import re
from unittest.mock import patch

import pytest

from devtool.actions_control_center import (
    capability_inventory, capabilities_text, diagnostic_text, error_info,
)
from devtool.actions_dispatch import dispatch_resume_decision, dispatch_workflow
from devtool.github_actions import GitHubActionsError, GitHubActionsClient
from devtool.cli import build_parser
from tests.test_actions_02_workflow_dispatch import prepared

SHA = 'a'*40
REQ = 'b'*32


def _repo(tmp_path, *, hosted=True):
    root=tmp_path/'repo';root.mkdir()
    config='''[project]
runner="python"
[targets.android]
runner="gradle"
platform="android"
'''
    if hosted:
        config+='actions_only=true\n'
    config+='''[targets.server]
runner="python"
[wrapper.commands.android-build]
devtool="actions"
'''
    (root/'.devtool.toml').write_text(config)
    return root


@pytest.mark.parametrize('gradle_present',[True,False])
def test_hosted_android_policy_not_reclassified_by_path(tmp_path, monkeypatch, gradle_present):
    root=_repo(tmp_path)
    import devtool.actions_control_center as cc
    genuine=cc.shutil.which
    monkeypatch.setattr(cc.shutil,'which',lambda cmd: '/usr/bin/gradle' if cmd=='gradle' and gradle_present else None if cmd=='gradle' else genuine(cmd))
    before=(root/'.devtool.toml').read_bytes()
    data=capability_inventory(root)
    assert (root/'.devtool.toml').read_bytes()==before
    assert data['schema']=='DevtoolCapabilitiesV1'
    assert data['capabilities_v2']['schema']=='DevtoolCapabilitiesV2'
    assert data['prerequisites']['gradle']['available'] is gradle_present
    op=next(x for x in data['capabilities_v2']['operations'] if x['target']=='android')
    assert op['toolchain']['state']==('installed' if gradle_present else 'missing')
    assert op['local']['status']=='blocked'
    assert op['hosted']['status']=='unknown'
    assert op['policy']['github_actions_only']
    assert op['evidence']['status']=='NOT_RUN'
    assert set(op['authorization'].values())=={'NOT_RUN'}
    assert data['capabilities_v2']['hosted_status']=='not-queried'
    assert not op['qualified_release']


def test_configured_local_unqualified_and_wrapper_retained(tmp_path):
    data=capability_inventory(_repo(tmp_path,hosted=False))
    assert data['targets']==['android','server']
    assert 'android-build' in data['wrappers']
    op=data['capabilities_v2']['operations'][0]
    assert op['hosted']['status']=='unknown' and not op['hosted']['configured']
    assert not op['toolchain']['qualified']


@pytest.mark.parametrize('width',[40,44,60])
def test_text_json_identity_parity_and_terminal_width(tmp_path,width):
    data=capability_inventory(_repo(tmp_path))
    rendered=capabilities_text(data,width=width)
    assert max(map(len,rendered.splitlines())) <= width
    for x in data['capabilities_v2']['operations']:
        assert 'Target: '+x['target'] in rendered
        assert x['local']['status'] in rendered
        assert x['hosted']['status'] in rendered
    assert 'NOT QUALIFIED' in rendered
    assert '/tmp/' not in rendered
    assert 'GitHub only' in rendered


def test_cli_default_json_and_explicit_text_flags():
    parser=build_parser()
    old=parser.parse_args(['capabilities','--json'])
    assert old.command=='capabilities'
    assert parser.parse_args(['capabilities']).format=='json'
    new=parser.parse_args(['capabilities','--format','text','--width','40'])
    assert (new.format,new.width)==('text',40)


def test_typed_diagnostic_redacts_identifiers_and_secrets():
    secret='ghp_ThisIsNeverARealToken1234567890'
    row=error_info(secret,code='REQUEST_UNCERTAIN',target='android\x1b[31m'+secret,
        node='../../private',receipt=REQ,path='/data/data/com.termux/secrets/token',
        repository='owner/repo',operation='review',state='UNKNOWN')
    assert row['code']=='REQUEST_UNCERTAIN'
    assert row['receipt_id']==REQ
    assert row['affected_node'] is None and row['affected_target'] is None
    assert row['affected_path'] is None and row['receipt_path'] is None
    assert row['affected_repository']=='owner/repo'
    assert row['documentation_anchor'].endswith('#request-uncertain')
    assert row['retry_safe'] is False and row['qualified_release'] is False
    for width in (40,44,60):
        display=diagnostic_text(row,width=width)
        assert max(map(len,display.splitlines()))<=width
        assert row['code'] in display and REQ in display
        assert secret not in display and '/data/data' not in display
    assert secret not in json.dumps(row)


@pytest.mark.parametrize('state,run_id,decision',[
    ('accepted-correlated',103,'EXISTING_CORRELATED'),
    ('accepted-uncorrelated',None,'EXISTING_UNCORRELATED'),
    ('dispatch-outcome-unknown',None,'EXISTING_UNCORRELATED'),
    ('rejected-by-api',None,'REJECTED_BY_API'),
    ('accepted-correlated',None,'NEEDS_EXPLICIT_REVIEW'),
    ('accepted-uncorrelated',103,'NEEDS_EXPLICIT_REVIEW'),
])
def test_read_only_dispatch_decision_never_mutates(tmp_path,state,run_id,decision):
    path=tmp_path/(REQ+'.json')
    record={'schema':1,'request_id':REQ,'repository':'owner/repo','workflow_id':12,
            'pre_dispatch_sha':SHA,'ref':'main','state':state,'run_id':run_id}
    path.write_text(json.dumps(record));before=path.read_bytes()
    names_before=sorted(x.name for x in tmp_path.iterdir())
    got=dispatch_resume_decision(REQ,state_root=tmp_path)
    assert got['decision']==decision and not got['safe_to_redispatch']
    assert not got['qualified_release'] and got['read_only']
    assert got['run_id']==(103 if decision=='EXISTING_CORRELATED' else None)
    assert got['request_id']==REQ and got['source_sha']==SHA
    assert path.read_bytes()==before
    assert sorted(x.name for x in tmp_path.iterdir())==names_before


def test_reject_malformed_and_symlinked_dispatch_record(tmp_path):
    (tmp_path/(REQ+'.json')).symlink_to(tmp_path/'secret.json')
    with pytest.raises(GitHubActionsError):
        dispatch_resume_decision(REQ,state_root=tmp_path)
    with pytest.raises(GitHubActionsError):
        dispatch_resume_decision('../private',state_root=tmp_path)


def test_existing_uncertain_resume_does_not_post(tmp_path):
    from urllib.error import URLError
    first=prepared();first.replies[-1]=URLError('lost response')
    with pytest.raises(GitHubActionsError,match='uncertain'):
        dispatch_workflow(GitHubActionsClient('token',opener=first),
            'owner/repo','ci.yml','main',{'mode':'release'},execute=True,
            state_root=tmp_path,resume_if_existing=True,expected_source_sha=SHA)
    second=prepared();second.replies.pop()
    again=dispatch_workflow(GitHubActionsClient('token',opener=second),
        'owner/repo','ci.yml','main',{'mode':'release'},execute=True,
        state_root=tmp_path,resume_if_existing=True,expected_source_sha=SHA)
    assert again['resume_decision']=='EXISTING_UNCORRELATED'
    assert again['run_id'] is None and not again['dispatched']
    assert all(req.get_method()!='POST' for req in second.requests)


def test_existing_proven_run_reused_without_post(tmp_path):
    first=prepared()
    accepted=dispatch_workflow(GitHubActionsClient('token',opener=first),
        'owner/repo','ci.yml','main',{'mode':'release'},execute=True,
        state_root=tmp_path,resume_if_existing=True,expected_source_sha=SHA)
    second=prepared();second.replies.pop()
    again=dispatch_workflow(GitHubActionsClient('token',opener=second),
        'owner/repo','ci.yml','main',{'mode':'release'},execute=True,
        state_root=tmp_path,resume_if_existing=True,expected_source_sha=SHA)
    assert again['resume_decision']=='EXISTING_CORRELATED'
    assert again['run_id']==accepted['run_id']
    assert all(req.get_method()!='POST' for req in second.requests)


def test_absent_exact_request_is_no_match_not_success(tmp_path):
    row=dispatch_resume_decision(REQ,state_root=tmp_path)
    assert row['decision']=='NO_MATCH'
    assert row['run_id'] is None and not row['safe_to_redispatch']
    assert not row['attribution_proven']
    assert not list(tmp_path.glob('*.json'))


def test_workflows_wrappers_and_providers_have_independent_projections(tmp_path):
    data=capability_inventory(_repo(tmp_path))
    view=data['capabilities_v2']
    assert 'gradle' in view['configured_providers']
    assert 'python' in view['configured_providers']
    cmd=next(x for x in view['declared_operations'] if x['name']=='android-build')
    assert cmd['kind']=='wrapper' and cmd['configured']
    assert cmd['evidence']['status']=='NOT_RUN'
    assert cmd['hosted']['status']=='unknown'


def test_operator_review_output_bounded_and_diagnostic_propagated():
    from devtool.actions_operator import operator_review, operator_lines
    row={'kind':'dispatch','id':REQ,'repository':'owner/repo','run_id':None}
    review=operator_review(row,'owner/repo')
    assert review['diagnostic']['code']=='REQUEST_UNCERTAIN'
    assert review['diagnostic']['receipt_id']==REQ
    for width in (40,44,60):
        assert max(map(len,operator_lines(review,width=width).splitlines()))<=width
