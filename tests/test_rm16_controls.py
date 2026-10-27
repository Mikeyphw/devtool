"""RM16 source-bound deduplication and narrow terminal projections."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from devtool.actions_dispatch import dispatch_workflow
from devtool.github_actions import GitHubActionsClient,GitHubActionsError
from tests.test_actions_02_workflow_dispatch import prepared
from devtool.actions_control_center import overview_text, error_info, capability_inventory
from devtool.cli import build_parser


def test_second_matching_dispatch_does_not_post_again(tmp_path):
    first=prepared();source=GitHubActionsClient('token',opener=first)
    accepted=dispatch_workflow(source,'owner/repo','ci.yml','main',{'mode':'release'},
        execute=True,state_root=tmp_path,resume_if_existing=True,expected_source_sha='a'*40)
    assert accepted['run_id']==3434
    second=prepared();second.replies.pop() # no POST reply permitted
    duplicate=dispatch_workflow(GitHubActionsClient('token',opener=second),
        'owner/repo','ci.yml','main',{'mode':'release'},
        execute=True,state_root=tmp_path,resume_if_existing=True,expected_source_sha='a'*40)
    assert duplicate['resume_existing'] and not duplicate['dispatched']
    assert duplicate['request_id']==accepted['request_id']
    assert sum(x.get_method()=='POST' for x in first.requests)==1
    assert sum(x.get_method()=='POST' for x in second.requests)==0


def test_uncertain_dispatch_is_blocked_on_retry(tmp_path):
    from urllib.error import URLError
    first=prepared();first.replies[-1]=URLError('socket closed')
    with pytest.raises(GitHubActionsError,match='uncertain'):
        dispatch_workflow(GitHubActionsClient('token',opener=first),
            'owner/repo','ci.yml','main',{'mode':'release'},execute=True,state_root=tmp_path,
            resume_if_existing=True,expected_source_sha='a'*40)
    second=prepared();second.replies.pop()
    existing=dispatch_workflow(GitHubActionsClient('token',opener=second),
        'owner/repo','ci.yml','main',{'mode':'release'},execute=True,state_root=tmp_path,
        resume_if_existing=True,expected_source_sha='a'*40)
    assert existing['resume_existing'] and existing['run_id'] is None
    assert not any(x.get_method()=='POST' for x in second.requests)


def test_narrow_terminal_and_error_provenance():
    report={'repository':'owner/repo','branch':'feature/work','authentication':{'session_verified':False},
        'status':'uncorrelated','remote':{'state':'not-queried','artifact_verification':'not-verified'},
        'blocker_code':'REQUEST_UNCERTAIN','safe_next_command':'devtool actions resume --request-id '+('a'*32)}
    lines=overview_text(report,width=40).splitlines()
    assert lines and max(map(len,lines))<=40
    assert 'NOT QUALIFIED' in '\n'.join(lines)
    assert error_info('dispatch outcome uncertain')['retry_safe'] is False
    assert not error_info('artifact sha mismatch')['qualified_release']


def test_cli_rm16_projections():
    parser=build_parser()
    one=parser.parse_args(['actions','run','ci.yml','--github-repo','owner/repo','--ref','main',
                           '--resume-if-existing','--yes'])
    assert one.resume_if_existing
    two=parser.parse_args(['actions','overview','--github-repo','owner/repo','--width','40','--json'])
    assert two.actions_command=='overview' and two.width==40
    three=parser.parse_args(['capabilities','--json'])
    assert three.command=='capabilities'


def test_typed_actions_wrapper_dispatch_requires_user_yes(tmp_path):
    from devtool.config import load_project_config
    from devtool.wrapper_resolution import resolve_wrapper_argv
    from unittest import mock
    root=tmp_path
    (root/'.devtool.toml').write_text('''
[project]
runner = "command"
[targets.mobile]
runner = "command"
[wrapper.commands.remote]
devtool = "actions"
[wrapper.commands.remote.actions]
operation = "dispatch"
workflow_id = "bkx-unsigned.yml"
ref = "main"
[wrapper.commands.remote.actions.inputs]
mode = "release"
[wrapper.commands.remote.options.yes]
type = "boolean"
command_argv = ["--yes"]
''')
    with mock.patch.dict('os.environ', {'DEVTOOL_GLOBAL_CONFIG':str(root/'no-global.toml')}):
        config=load_project_config(str(root),None,allow_ambiguous_target=True)
    r=resolve_wrapper_argv(config,['remote','--yes'])
    assert r.canonical_argv[-1]=='--yes'
    assert ('run','bkx-unsigned.yml','--ref','main','--resume-if-existing')==r.command_argv[:5]
    assert not any(x in r.canonical_argv for x in ('sh','-c','bash'))


def test_actions_wrapper_ref_and_passthrough_refused(tmp_path):
    from devtool.config import load_project_config
    from unittest import mock
    root=tmp_path
    template='''
[project]
runner = "command"
[targets.mobile]
runner = "command"
[wrapper.commands.remote]
devtool = "actions"
allow_passthrough = {passthrough}
[wrapper.commands.remote.actions]
operation = "dispatch"
workflow_id = "bkx-unsigned.yml"
ref = "{ref}"
[wrapper.commands.remote.options.yes]
type = "boolean"
command_argv = ["--yes"]
'''
    for passthrough,ref in [('true','main'),('false','refs/heads/main')]:
        (root/'.devtool.toml').write_text(template.format(passthrough=passthrough,ref=ref))
        with mock.patch.dict('os.environ', {'DEVTOOL_GLOBAL_CONFIG':str(root/'no-global.toml')}):
            with pytest.raises((ValueError,RuntimeError,SystemExit)):
                load_project_config(str(root),None,allow_ambiguous_target=True)


def test_error_codes_remain_distinct_and_paths_safe():
    from devtool.actions_control_center import error_info
    assert error_info('requires GitHub authentication')['code']=='AUTH_MISSING'
    assert error_info('workflow not found')['code']=='WORKFLOW_MISSING'
    assert error_info('connection failed')['code']=='REMOTE_UNAVAILABLE'
    assert error_info('dispatch outcome uncertain')['code']=='REQUEST_UNCERTAIN'
    assert error_info('connection failed',path='devtool/actions_dispatch.py')['affected_path']=='devtool/actions_dispatch.py'
    assert error_info('connection failed',path='/secret/private')['affected_path'] is None
