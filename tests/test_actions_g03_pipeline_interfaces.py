"""G3: pipeline Textual/GTK/HUD views have one journal truth and guarded controls."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from devtool.actions_presentation import project_pipeline
from devtool.actions_pipeline_textual import pipeline_screen_lines
from devtool.desktop.command_model import DevtoolCommandDraft
from devtool.desktop.run_workflow import payload_from_mapping, review_payload, workflow_snapshot

PIPELINE_ID = 'a'*32
SOURCE_SHA = 'b'*40


def sample(*states, **overrides):
    result = {'phase':'ACTIONS-06','pipeline_id':PIPELINE_ID,'name':'g3-pipeline',
              'repository':'Mikeyphw/devtool','source_sha':SOURCE_SHA,
              'completed_successfully':False,'qualified_release':False,
              'nodes':[{'id':f'n{i}','kind':'github-result','state':state} for i,state in enumerate(states)]}
    result.update(overrides)
    return result


def test_screen_matches_authoritative_states_and_does_not_promote_to_release():
    for states in [('waiting','success'),('running','pending'),('uncertain','waiting'),('failure',),('cancelled',)]:
        report = sample(*states)
        text = pipeline_screen_lines(report)
        projected = project_pipeline(report)
        assert text['state'] == projected['state']
        assert 'NOT QUALIFIED' in text['note']
        for state in states:
            assert state.upper() in text['nodes']
    assert 'NOT cancelled' in pipeline_screen_lines(sample('waiting',cancel_requested=True))['note']


def test_screen_strips_ansi_and_markup_is_not_interpreted():
    data=sample('blocked')
    data['nodes'][0]['detail']='\x1b[31m[bold]bad[/bold]\x1b[0m\x00'
    view=pipeline_screen_lines(data)
    assert '\x1b' not in view['nodes'] and '\x00' not in view['nodes']
    assert '[bold]bad[/bold]' in view['nodes']


def test_screen_rejects_false_success_and_unexpected_identity():
    x=sample('success',completed_successfully=False)
    assert pipeline_screen_lines(x)['state']!='success'
    with pytest.raises(ValueError):pipeline_screen_lines({'phase':'invalid','nodes':[]})


def test_desktop_pipeline_stop_is_explicit_and_typed():
    assert 'pipeline-cancel' in {x['id'] for x in workflow_snapshot({'repository':{'path':'/repo'}}).operations}
    base={'repository':'/repo','operation':'pipeline-cancel','pipeline_id':PIPELINE_ID,'yes':False}
    rejected=review_payload(payload_from_mapping(base))
    assert not rejected.argv and not rejected.runnable
    base['yes']=True
    approved=review_payload(payload_from_mapping(base))
    assert approved.runnable
    assert approved.argv[approved.argv.index('actions'):][:5] == ['actions','pipeline','cancel','--pipeline-id',PIPELINE_ID]
    assert approved.argv[-1]=='--yes'
    draft=DevtoolCommandDraft(repository='/repo',operation='pipeline-cancel',pipeline_id='../invalid',yes=True)
    assert draft.argv()==[]


def test_cli_status_tty_uses_screen_but_json_and_non_tty_never_launch(monkeypatch,capsys):
    from devtool.actions_cli import run_actions_command
    import devtool.actions_pipeline_textual as screen
    payload=sample('waiting')
    called=[]
    monkeypatch.setattr('devtool.actions_cli.actions_cli',lambda *_: payload)
    monkeypatch.setattr('sys.stdout.isatty',lambda:True)
    monkeypatch.setattr('sys.stdin.isatty',lambda:True)
    monkeypatch.setattr('devtool.textual_execution_host.textual_available',lambda:True)
    monkeypatch.setattr(screen,'run_pipeline_app',lambda data:called.append(data))
    monkeypatch.delenv('CI',raising=False)
    monkeypatch.delenv('DEVTOOL_EXECUTION_PRESENTATION',raising=False)
    args=SimpleNamespace(repo='/repo',json=False,_devtool_json=False,actions_command='pipeline',pipeline_command='status')
    assert run_actions_command(args)==0 and called==[payload]
    called.clear();args.json=True
    assert run_actions_command(args)==0 and not called
    assert json.loads(capsys.readouterr().out)['phase']=='ACTIONS-06'
    args.json=False
    monkeypatch.setattr('sys.stdin.isatty',lambda:False)
    assert run_actions_command(args)==0 and not called
    assert 'Pipeline: g3-pipeline' in capsys.readouterr().out


def test_real_textual_screen_narrow_refresh_and_failure(monkeypatch):
    pytest.importorskip('textual')
    from devtool.actions_pipeline_textual import create_pipeline_app
    from textual.widgets import Static
    import asyncio
    snapshots=[sample('waiting'),sample('success', completed_successfully=True)]
    count=[]
    def load():
        count.append(1)
        if len(count)==1:raise ValueError('journal temporarily missing')
        return snapshots[min(len(count),1)]
    app=create_pipeline_app(PIPELINE_ID,load=load,initial=snapshots[0])
    async def check():
        async with app.run_test(size=(34,24)) as pilot:
            assert 'WAITING' in str(app.query_one('#headline',Static).render())
            await pilot.press('r');await pilot.pause()
            assert 'Refresh failed' in str(app.query_one('#note',Static).render())
            assert 'WAITING' in str(app.query_one('#headline',Static).render())
            await pilot.press('r');await pilot.pause()
            assert 'SUCCESS' in str(app.query_one('#headline',Static).render())
            assert 'NOT QUALIFIED' in str(app.query_one('#note',Static).render())
    asyncio.run(check())


def test_android_hud_builder_parity_compiles_and_roundtrips(tmp_path):
    javac=shutil.which('javac');java=shutil.which('java')
    if not javac or not java:pytest.skip('JDK unavailable')
    root=Path(__file__).resolve().parents[1]/'android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud'
    sample_java=tmp_path/'G03HudCheck.java'
    sample_java.write_text('''package io.github.mikeceead.devtoolhud;
public final class G03HudCheck {
 static void check(boolean ok){if(!ok)throw new AssertionError();}
 public static void main(String[] args){
  DevtoolRunConfig c=new DevtoolRunConfig();c.repo="$HOME/Code/devtool";c.autoHud=false;c.copy=false;c.copyViaTermux=false;
  c.operation="pipeline-cancel";c.pipelineId="a".repeat(32);c.assumeYes=false;
  boolean rejected=false;try{DevtoolCommandBuilder.build(c);}catch(IllegalArgumentException e){rejected=true;}
  check(rejected);c.assumeYes=true;
  String cmd=DevtoolCommandBuilder.build(c);
  check(cmd.contains("actions pipeline cancel --pipeline-id "+c.pipelineId+" --yes"));
  DevtoolCommandImporter.ImportResult imported=DevtoolCommandImporter.parse(cmd);
  check(imported.config.operation.equals("pipeline-cancel"));
  check(imported.config.pipelineId.equals(c.pipelineId));
  c.pipelineId="../unsafe";rejected=false;try{DevtoolCommandBuilder.build(c);}catch(IllegalArgumentException e){rejected=true;}
  check(rejected);
 }
}
''')
    classes=tmp_path/'classes';classes.mkdir()
    cmd=[javac,'-d',str(classes),*[str(root/f) for f in ('DevtoolRunConfig.java','DevtoolCommandBuilder.java','DevtoolCommandImporter.java')],str(sample_java)]
    out=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
    assert out.returncode==0,out.stderr
    out=subprocess.run([java,'-cp',str(classes),'io.github.mikeceead.devtoolhud.G03HudCheck'],capture_output=True,text=True,timeout=15)
    assert out.returncode==0,out.stderr
