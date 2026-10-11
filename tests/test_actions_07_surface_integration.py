"""ACTIONS-07: truthful cross-surface semantics and reviewed action construction."""
from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys
from unittest.mock import Mock

import pytest

from devtool.actions_presentation import project_pipeline, pipeline_text
from devtool.desktop.command_model import DevtoolCommandDraft
from devtool.desktop.run_workflow import payload_from_mapping, review_payload, workflow_snapshot


def payload(*states, **overrides):
    data={'phase':'ACTIONS-06','pipeline_id':'a'*32,'name':'live-hud',
          'repository':'Mikeyphw/devtool','source_sha':'b'*40,
          'completed_successfully':False,'qualified_release':False,
          'nodes':[{'id':f'n{i}','kind':'github-result','state':s} for i,s in enumerate(states)]}
    data.update(overrides)
    return data


def test_waiting_not_success_and_resume_possible():
    view=project_pipeline(payload('success','waiting'))
    assert view['state']=='waiting' and not view['completed_successfully']
    assert view['resume_enabled'] and not view['dispatch_enabled'] and not view['qualified_release']
    assert 'NOT QUALIFIED' in pipeline_text(payload('waiting'))


@pytest.mark.parametrize('bad', ['failure','blocked','uncertain'])
def test_blocked_cannot_auto_resume_or_redispatch(bad):
    view=project_pipeline(payload('success',bad))
    assert view['state']=='blocked' and not view['resume_enabled']
    assert 'manual reconciliation' in pipeline_text(payload(bad))


def test_running_not_safe_to_resume():
    assert not project_pipeline(payload('running'))['resume_enabled']


def test_success_requires_all_nodes_and_evidence():
    assert not project_pipeline(payload('success','success'))['completed_successfully']
    assert project_pipeline(payload('success','success',completed_successfully=True))['completed_successfully']
    assert project_pipeline(payload('success','success',completed_successfully=True))['qualified_release'] is False


def test_projection_rejects_invalid_source_or_state():
    with pytest.raises(ValueError): project_pipeline({'phase':'other','nodes':[]})
    with pytest.raises(ValueError): project_pipeline(payload('unknown'))


@pytest.mark.parametrize('mode,field,value', [
 ('pipeline-plan','pipeline_file','.devtool/actions-pipeline.json'),
 ('pipeline-run','pipeline_file','.devtool/actions-pipeline.json'),
 ('pipeline-status','pipeline_id','a'*32),
 ('pipeline-resume','pipeline_id','a'*32),
])
def test_desktop_typed_modes(mode,field,value):
    draft=DevtoolCommandDraft(repository='/repo', operation=mode, yes=True, copy_mode='none',auto_hud=False)
    setattr(draft,field,value)
    argv=draft.argv()
    assert argv[:3]==['devtool','--no-clipboard','--no-hud']
    assert argv.count('actions')==1
    assert argv[argv.index('actions')+1:argv.index('actions')+3]==['pipeline',mode.split('-')[1]]
    if mode in ('pipeline-run','pipeline-resume'):
        assert '--yes' in argv


def test_desktop_mutation_fail_closed_and_path_validation():
    draft=DevtoolCommandDraft(repository='/repo',operation='pipeline-run',yes=False)
    assert draft.argv()==[]
    for unsafe in ('../manifest.json','x/../p.json','x/./p.json','/abs/p.json','a b.json'):
        draft=DevtoolCommandDraft(repository='/repo',operation='pipeline-plan',pipeline_file=unsafe)
        assert draft.argv()==[]
    draft=DevtoolCommandDraft(repository='/repo',operation='pipeline-status',pipeline_id='G'*32)
    assert draft.argv()==[]


def test_desktop_run_review_requires_explicit_approval():
    p=payload_from_mapping({'repository':'/repo','operation':'pipeline-run',
        'pipeline_file':'.devtool/actions-pipeline.json','yes':False})
    result=review_payload(p)
    assert not result.runnable and any('--yes' in err for err in result.errors)
    p=payload_from_mapping({'repository':'/repo','operation':'pipeline-run',
        'pipeline_file':'.devtool/actions-pipeline.json','yes':True})
    assert review_payload(p).runnable


def test_desktop_workflow_lists_four_pipeline_operations():
    operations={x['id'] for x in workflow_snapshot({'repository':{'path':'/repo'}}).operations}
    assert {'pipeline-plan','pipeline-run','pipeline-status','pipeline-resume'} <= operations


def test_termux_cli_human_render_only_for_tty(monkeypatch,capsys):
    from devtool.actions_cli import run_actions_command
    from types import SimpleNamespace
    monkeypatch.setattr('devtool.actions_cli.actions_cli',lambda *_:payload('waiting'))
    monkeypatch.setattr('sys.stdout.isatty',lambda:True)
    args=SimpleNamespace(repo='/repo',json=False,_devtool_json=False,actions_command='pipeline')
    assert run_actions_command(args)==0
    assert 'Pipeline: live-hud [waiting]' in capsys.readouterr().out
    args.json=True
    assert run_actions_command(args)==0
    assert json.loads(capsys.readouterr().out)['phase']=='ACTIONS-06'
