"""RM04 tests assert actual hashes, retry selection and refused side effects."""
from __future__ import annotations
import hashlib,json,os,subprocess,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from devtool.models import CommandSpec, OperationResult
from devtool.workflow_retry import (WorkflowRetryError,capture_start,capture_finish,
    plan_retry,execute_retry,_record_path,_read,_path_snapshot)
from devtool.cli import build_parser

RUN='rm04-original-run'

def git(root,*args):
    p=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    return p.stdout.strip()

def fixture(tmp_path,monkeypatch):
    root=tmp_path/'repo';root.mkdir()
    git(root,'init','-q');git(root,'config','user.name','Devtool Tests');git(root,'config','user.email','devtool@example.invalid')
    (root/'src').mkdir();(root/'src/input.txt').write_text('input')
    (root/'.devtool.toml').write_text('[project]\nrunner="command"\n')
    git(root,'add','.devtool.toml','src/input.txt');git(root,'commit','-qm','fixture')
    monkeypatch.setenv('DEVTOOL_EXECUTION_PROJECTION_DIR',str(tmp_path/'exo'))
    monkeypatch.setenv('DEVTOOL_EXECUTION_HISTORY_DIR',str(tmp_path/'history'))
    return root

def make_specs(root,unsafe=False):
    specs=[]
    for key,deps in [('a',[]),('b',[]),('c',['b'])]:
        meta={'workflow_graph':True,'workflow_node_key':'api.ci.'+key,
              'workflow_target':'api','workflow_name':'ci','workflow_attempt':1,
              'workflow_config_fingerprint':'sha-'+key,
              'workflow_depends_on':['api.ci.'+k for k in deps],
              'workflow_inputs':['src/**'],
              'workflow_outputs':['out/'+key+'.txt'],
              'retry_safe': not unsafe}
        specs.append(CommandSpec(name=key,argv=[sys.executable,'-c','print("ok")'],
                                 cwd=str(root),metadata=meta))
    return specs

def op(spec,code=0, state='passed'):
    return OperationResult(name=spec.name,argv=spec.argv,returncode=code,duration_seconds=0.01,
                           output='',issues=[],metadata={**spec.metadata,'workflow_node_state':state})

def prepare(tmp_path,monkeypatch,*,unsafe=False):
    root=fixture(tmp_path,monkeypatch);specs=make_specs(root,unsafe=unsafe)
    rec=capture_start(root,RUN,specs)
    (root/'out').mkdir()
    for key in ('a','c'):(root/'out'/f'{key}.txt').write_text(key)
    capture_finish(root,rec,[op(specs[0]),op(specs[1],1,'failed'),op(specs[2],1,'blocked')])
    # Preserve RM03's strict event/snapshot reconciliation. This is a genuine
    # serialized journal, not a mocked return from its lineage projection.
    d=tmp_path/'exo'/RUN;d.mkdir(parents=True,exist_ok=True)
    nodes=[];events=[]
    for i,(spec,status) in enumerate(zip(specs,['succeeded','failed','blocked']),1):
        key=spec.metadata['workflow_node_key']
        nodes.append({'node_id':key,'status':status,'depends_on':spec.metadata['workflow_depends_on'],
                      'metadata':{'workflow_target':'api','workflow_name':'ci'}})
        events.append({'event_type':'node.finished','run_id':RUN,'sequence':i,'node_id':key,'status':status})
    snapshot={'kind':'devtool.execution.snapshot','schema_version':1,'run_id':RUN,'sequence':3,
              'execution':{'run_id':RUN,'status':'failed','nodes':nodes,
                           'repository_identity':{'commit':git(root,'rev-parse','HEAD')}}}
    (d/'execution.json').write_text(json.dumps(snapshot));(d/'execution-events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in events))
    return root,specs


def test_review_and_replay_only_failed_and_descendants(tmp_path,monkeypatch):
    root,specs=prepare(tmp_path,monkeypatch)
    plan=plan_retry(root,RUN,specs)
    assert plan['disposition']=='READY'
    assert plan['reuse']==['api.ci.a']
    assert plan['rerun']==['api.ci.b','api.ci.c']
    seen=[]
    def replay(parts):
        seen.extend(parts)
        for s in parts:
            (root/'out'/f"{s.name}.txt").write_text(s.name)
        return [op(s) for s in parts]
    result=execute_retry(root,plan,specs,approval=True,original_run_id=RUN,on_execute=replay)
    assert result['execution_verified'] and result['state']=='COMPLETED'
    assert [x.metadata['workflow_node_key'] for x in seen]==['api.ci.b','api.ci.c']
    assert seen[1].metadata['workflow_depends_on']==['api.ci.b']
    assert all(x.metadata['workflow_attempt']==2 for x in seen)
    assert _read(_record_path(RUN).parent/'workflow-retry-attempt.json')['execution_verified']


def test_tampered_output_invalidates_reuse_and_downstream(tmp_path,monkeypatch):
    root,specs=prepare(tmp_path,monkeypatch)
    (root/'out/a.txt').write_text('tampered')
    plan=plan_retry(root,RUN,specs)
    assert 'api.ci.a' in plan['rerun']
    assert 'api.ci.a' not in plan['reuse']


def test_source_and_input_drift_fail_closed(tmp_path,monkeypatch):
    root,specs=prepare(tmp_path,monkeypatch)
    (root/'src/input.txt').write_text('different')
    with pytest.raises(WorkflowRetryError,match='tracked source drift'):
        plan_retry(root,RUN,specs)
    git(root,'checkout','--','src/input.txt')
    (root/'src/extra.txt').write_text('new')
    plan=plan_retry(root,RUN,specs)
    assert plan['disposition']=='BLOCKED' and plan['blocked']


def test_no_retry_for_unsafe_and_unknown_remote(tmp_path,monkeypatch):
    root,specs=prepare(tmp_path,monkeypatch,unsafe=True)
    plan=plan_retry(root,RUN,specs)
    assert plan['disposition']=='BLOCKED' and plan['blocked']
    with pytest.raises(WorkflowRetryError):
        execute_retry(root,plan,specs,approval=True,original_run_id=RUN,on_execute=lambda x:())


def test_command_fingerprint_change_refuses(tmp_path,monkeypatch):
    root,specs=prepare(tmp_path,monkeypatch)
    specs[1].argv=['bash','-c','echo different']
    with pytest.raises(WorkflowRetryError,match='fingerprint'):
        plan_retry(root,RUN,specs)


def test_journal_truncation_and_terminal_disagreement_fail_closed(tmp_path,monkeypatch):
    root,specs=prepare(tmp_path,monkeypatch)
    journal=tmp_path/'exo'/RUN/'execution-events.jsonl'
    journal.write_text(journal.read_text().splitlines()[0]+'\n')
    with pytest.raises(WorkflowRetryError,match='incomplete'):
        plan_retry(root,RUN,specs)


def test_replay_interruption_is_not_silently_repeated(tmp_path,monkeypatch):
    root,specs=prepare(tmp_path,monkeypatch)
    plan=plan_retry(root,RUN,specs)
    def crash(_): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        execute_retry(root,plan,specs,approval=True,original_run_id=RUN,on_execute=crash)
    entry=_read(_record_path(RUN).parent/'workflow-retry-attempt.json')
    assert entry['state']=='RUNNING'
    with pytest.raises(WorkflowRetryError,match='unfinished'):
        execute_retry(root,plan,specs,approval=True,original_run_id=RUN,on_execute=lambda x:())


def test_wrong_attempt_approval_and_host_fail_closed(tmp_path,monkeypatch):
    root,specs=prepare(tmp_path,monkeypatch)
    plan=plan_retry(root,RUN,specs)
    with pytest.raises(WorkflowRetryError,match='approval'):
        execute_retry(root,plan,specs,approval=False,original_run_id=RUN,on_execute=lambda x:())
    monkeypatch.setattr('devtool.workflow_retry._host',lambda:'different-host')
    with pytest.raises(WorkflowRetryError,match='host'):
        plan_retry(root,RUN,specs)


def test_cli_parser_has_readonly_vs_explicit_replay():
    args=build_parser().parse_args(['workflow','retry','--run-id',RUN,'--failed','--plan'])
    assert args.workflow_command=='retry' and args.plan and not args.yes
    args=build_parser().parse_args(['workflow','retry','--run-id',RUN,'--failed','--yes'])
    assert args.yes and not args.plan

def test_real_native_scheduler_persists_source_and_output_proofs(tmp_path,monkeypatch):
    from devtool.config import load_project_config
    from devtool.workflow_runtime import build_workspace_specs
    from devtool.workflow_scheduler import run_workflow_specs
    root=fixture(tmp_path,monkeypatch)
    monkeypatch.setenv('DEVTOOL_RUN_ID','rm04-live-normal')
    import tomllib
    # Two actual target-local command jobs, one producing an artifact and one
    # failing, through the live scheduler rather than a mocked executor.
    manifest=['[project]','runner="command"','[workspace]',
              'default_target="app"','[workspace.scheduler]',
              'parallel=true','max_parallel_nodes=2']
    for key,code in [('a',0),('b',1)]:
        cmd=[sys.executable,'-c',f'import pathlib,sys;pathlib.Path("out").mkdir(exist_ok=True);pathlib.Path("out/{key}.txt").write_text("{key}");sys.exit({code})']
        manifest+=['[jobs.'+key+']','runner="command"','root="."',
                   'command='+json.dumps(cmd),'inputs=["src/**"]',
                   'outputs=["out/'+key+'.txt"]','[jobs.'+key+'.metadata]','retry_safe=true']
    manifest+=['[targets.app]','runner="command"','[targets.app.workflows]',
               'ci=["job:a","job:b"]']
    (root/'.devtool.toml').write_text('\n'.join(manifest)+'\n')
    git(root,'add','.devtool.toml');git(root,'commit','-qm','workflow config')
    cfg=load_project_config(str(root),str(root/'.devtool.toml'),target_name='app')
    specs=build_workspace_specs(cfg,['app'],'ci')
    results,raw,plan=run_workflow_specs(cfg,specs,verbose=False,dry_run=False,
        continue_on_error=True,log_path=None,parallel_requested=True,max_parallel_nodes=2)
    assert len(results)==2 and sorted(x.returncode for x in results)==[0,1]
    record=_read(_record_path('rm04-live-normal'))
    assert record['terminal'] is True
    assert any(node['status']=='passed' and node['output_state']
               for node in record['nodes'].values())
    assert any(node['status']=='failed' for node in record['nodes'].values())
    assert all(node['signature'] and node['toolchain'] for node in record['nodes'].values())

def test_crash_resume_preserves_completed_node_without_duplicate(tmp_path,monkeypatch):
    root,specs=prepare(tmp_path,monkeypatch)
    plan=plan_retry(root,RUN,specs)
    def interrupted(parts,observer):
        b=next(s for s in parts if s.metadata['workflow_node_key']=='api.ci.b')
        observer('api.ci.b','started',[])
        (root/'out/b.txt').write_text('completed exactly once')
        observer('api.ci.b','terminal',[op(b)])
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        execute_retry(root,plan,specs,approval=True,original_run_id=RUN,on_execute=interrupted)
    state=_read(_record_path(RUN).parent/'workflow-retry-attempt.json')
    assert state['nodes']['api.ci.b']['state']=='PASSED'
    seen=[]
    def resumed(parts,observer):
        seen.extend(s.metadata['workflow_node_key'] for s in parts)
        for s in parts:
            (root/'out'/f"{s.name}.txt").write_text(s.name)
        return [op(s) for s in parts]
    result=execute_retry(root,plan,specs,approval=True,original_run_id=RUN,
                         resume=True,on_execute=resumed)
    assert seen==['api.ci.c']
    assert (root/'out/b.txt').read_text()=='completed exactly once'
    assert result['state']=='COMPLETED' and result['execution_verified']
    with pytest.raises(WorkflowRetryError,match='already completed'):
        execute_retry(root,plan,specs,approval=True,original_run_id=RUN,
                      resume=False,on_execute=lambda items:())


def test_crash_resume_refuses_modified_completed_artifact(tmp_path,monkeypatch):
    root,specs=prepare(tmp_path,monkeypatch)
    plan=plan_retry(root,RUN,specs)
    def partial(parts,observer):
        b=next(s for s in parts if s.metadata['workflow_node_key']=='api.ci.b')
        (root/'out/b.txt').write_text('original')
        observer('api.ci.b','terminal',[op(b)])
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        execute_retry(root,plan,specs,approval=True,original_run_id=RUN,on_execute=partial)
    (root/'out/b.txt').write_text('attacker changed')
    with pytest.raises(WorkflowRetryError,match='completed retry output changed'):
        execute_retry(root,plan,specs,approval=True,original_run_id=RUN,
                      resume=True,on_execute=lambda items:())

def test_canonical_cli_plan_uses_real_config_and_keeps_source_unchanged(tmp_path,monkeypatch,capsys):
    from devtool.config import load_project_config
    from devtool.workflow_runtime import build_workspace_specs
    from devtool.cli import main
    root=fixture(tmp_path,monkeypatch)
    cmd=[sys.executable,'-c','print("ok")']
    cfg_text='''[project]
runner = "command"
[targets.app]
runner = "command"
[targets.app.workflows]
ci = ["job:a", "job:b"]
'''
    for key in ('a','b'):
        node_cmd=[sys.executable,'-c',
                  f'import pathlib;pathlib.Path("out").mkdir(exist_ok=True);pathlib.Path("out/{key}.txt").write_text("{key}")']
        cfg_text+=f'''[jobs.{key}]
runner = "command"
command = {json.dumps(node_cmd)}
inputs = ["src/**"]
outputs = ["out/{key}.txt"]
[jobs.{key}.metadata]
retry_safe = true
'''
    (root/'.devtool.toml').write_text(cfg_text)
    git(root,'add','.devtool.toml');git(root,'commit','-qm','native cli config')
    cfg=load_project_config(str(root),str(root/'.devtool.toml'),target_name='app')
    specs=build_workspace_specs(cfg,['app'],'ci')
    rec=capture_start(root,RUN,specs)
    (root/'out').mkdir()
    (root/'out/a.txt').write_text('a')
    capture_finish(root,rec,[op(specs[0]),op(specs[1],1,'failed')])
    d=tmp_path/'exo'/RUN;d.mkdir(parents=True,exist_ok=True)
    source_commit=git(root,'rev-parse','HEAD')
    nodes=[{'node_id':s.metadata['workflow_node_key'],'status':v,
            'depends_on':s.metadata['workflow_depends_on'],
            'metadata':{'workflow_target':'app','workflow_name':'ci'}}
           for s,v in zip(specs,['succeeded','failed'])]
    events=[{'event_type':'node.finished','run_id':RUN,'sequence':i,
             'node_id':node['node_id'],'status':node['status']}
            for i,node in enumerate(nodes,1)]
    (d/'execution.json').write_text(json.dumps({'kind':'devtool.execution.snapshot',
        'schema_version':1,'run_id':RUN,'sequence':2,
        'execution':{'run_id':RUN,'status':'failed','nodes':nodes,
                     'repository_identity':{'commit':source_commit}}}))
    (d/'execution-events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
    old_tree=git(root,'rev-parse','HEAD^{tree}')
    import os
    package_root=Path(__file__).resolve().parents[1]
    env={**os.environ,'PYTHONPATH':str(package_root),
         'DEVTOOL_XDG_DIR':str(tmp_path/'cli-state'),
         'DEVTOOL_JOB_UI':'plain'}
    env.pop('DEVTOOL_RUN_ID',None)
    proc=subprocess.run([sys.executable,'-m','devtool','-r',str(root),'--target','app',
                         'workflow','retry','--run-id',RUN,'--failed','--plan'],
                         cwd=str(root),env=env,stdin=subprocess.DEVNULL,
                         text=True,capture_output=True,timeout=25,start_new_session=True)
    assert proc.returncode==0,proc.stdout+'\n'+proc.stderr
    payload=json.loads(proc.stdout)
    assert payload['disposition']=='READY' and len(payload['reuse'])==1 and len(payload['rerun'])==1
    assert git(root,'rev-parse','HEAD^{tree}')==old_tree
    assert not (_record_path(RUN).parent/'workflow-retry-attempt.json').exists()
    # Exercise the actual user-facing mutating CLI and its native scheduler,
    # not merely the imported retry API or parser.
    replay=subprocess.run([sys.executable,'-m','devtool','-r',str(root),'--target','app',
                           'workflow','retry','--run-id',RUN,'--failed','--yes'],
                          cwd=str(root),env=env,stdin=subprocess.DEVNULL,
                          capture_output=True,text=True,timeout=30,start_new_session=True)
    assert replay.returncode==0,replay.stdout+'\n'+replay.stderr
    report=json.loads(replay.stdout)
    assert report['state']=='COMPLETED' and report['execution_verified']
    key=specs[1].metadata['workflow_node_key']
    assert report['nodes'][key]['state']=='PASSED'
    assert (root/'out/b.txt').read_text()=='b'
    assert git(root,'rev-parse','HEAD^{tree}')==old_tree
    # Replaying an already completed attempt must not invoke its command twice.
    duplicate=subprocess.run([sys.executable,'-m','devtool','-r',str(root),'--target','app',
                              'workflow','retry','--run-id',RUN,'--failed','--yes'],
                             cwd=str(root),env=env,stdin=subprocess.DEVNULL,
                             capture_output=True,text=True,timeout=30,start_new_session=True)
    assert duplicate.returncode != 0
    assert git(root,'rev-parse','HEAD^{tree}')==old_tree
