import hashlib
import json
from pathlib import Path

import pytest

from devtool.remote_evidence_jobs import RemoteEvidenceError, verify_local_evidence, main as evidence_cli
from devtool.execution_lineage_v2 import LineageError, project_execution_lineage
from devtool.cli import build_parser
from devtool.config import load_project_config
from devtool.workflow_runtime import _job_specs

SHA = 'a' * 40
RUN = 'workflow-run-one'
NODE = 'api.ci.job'


def _receipt(tmp_path: Path, kind='artifact-verified'):
    content = b'artifact data!'
    (tmp_path / 'app.bin').write_bytes(content)
    payload = {
        'schema_version': 1, 'kind': 'devtool.remote-observation', 'job_kind':kind,
        'repository':'org/project', 'source_commit':SHA, 'exo_run_id':RUN,
        'exo_node_id':NODE,'request_id':'req-123', 'remote_run_id': 24, 'remote_attempt': 2,
        'remote':{'repository':'org/project','source_commit':SHA,'run_id':24,
                  'attempt':2,'state':'SUCCEEDED_OBSERVED'},
        'build':{'repository':'org/project','source_commit':SHA,'run_id':24,
                 'attempt':2,'request_id':'req-123','outcome':'success'},
        'artifact':{'repository':'org/project','source_commit':SHA,'run_id':24,
                    'attempt':2,'sha256':hashlib.sha256(content).hexdigest(), 'path':'app.bin'},
    }
    (tmp_path / 'receipt.json').write_text(json.dumps(payload))
    return payload


def _snapshot(tmp_path: Path, *, run=RUN, journal=True, term=True):
    root=tmp_path/'exo'
    directory=root/run
    directory.mkdir(parents=True,exist_ok=True)
    status='succeeded' if term else 'running'
    snapshot={'schema_version':1,'kind':'devtool.execution.snapshot','run_id':run,'sequence':2 if term else 1,
        'execution':{'run_id':run,'status':status,'repository_identity':{'commit':SHA},
          'nodes':[{'node_id':NODE,'status':status,'started_at':'now',
                    'duration_seconds':0.25,'depends_on':[],
                    'metadata':{'workflow_target':'api','workflow_name':'ci'}}]}}
    (directory/'execution.json').write_text(json.dumps(snapshot))
    if journal:
        entries=[{'event_type':'node.started','run_id':run,'sequence':1,'node_id':NODE}]
        if term:
            entries.append({'event_type':'node.finished','run_id':run,'sequence':2,'node_id':NODE,'status':status})
        (directory/'execution-events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in entries))
    return root


def test_typed_remote_verification_checks_real_bytes_and_never_grants_authority(tmp_path):
    _receipt(tmp_path)
    for kind in ['remote-run-correlated','remote-build-verified','artifact-verified']:
        result=verify_local_evidence(tmp_path,kind,'receipt.json',expected_run=RUN,expected_node=NODE)
        assert result['ok'] and result['remote_authenticated'] is False and result['release_qualified'] is False
        assert result['job_kind']==kind
    assert verify_local_evidence(tmp_path,'artifact-verified','receipt.json')['sha256_verified_locally'] == hashlib.sha256(b'artifact data!').hexdigest()


def test_remote_identity_fail_closed(tmp_path):
    receipt=_receipt(tmp_path)
    for field,patch in [('remote_attempt',3),('source_commit','b'*40),('exo_run_id','wrong')]:
        bad={**receipt,field:patch}
        (tmp_path/'receipt.json').write_text(json.dumps(bad))
        with pytest.raises(RemoteEvidenceError):
            verify_local_evidence(tmp_path,'artifact-verified','receipt.json', expected_run=RUN)
    (tmp_path/'receipt.json').write_text(json.dumps(receipt))
    (tmp_path/'app.bin').write_bytes(b'tampered')
    with pytest.raises(RemoteEvidenceError,match='digest'):
        verify_local_evidence(tmp_path,'artifact-verified','receipt.json')


def test_local_manifest_is_never_remote_authentication(tmp_path):
    receipt=_receipt(tmp_path)
    receipt['remote']['state']='INACCESSIBLE'
    (tmp_path/'receipt.json').write_text(json.dumps(receipt))
    verified=verify_local_evidence(tmp_path,'remote-run-correlated','receipt.json')
    assert verified['remote_observation']=='INACCESSIBLE'
    with pytest.raises(RemoteEvidenceError):
        verify_local_evidence(tmp_path,'remote-build-verified','receipt.json')


def test_unsafe_paths_rejected(tmp_path):
    receipt=_receipt(tmp_path)
    (tmp_path/'evil.json').symlink_to(tmp_path/'receipt.json')
    with pytest.raises(RemoteEvidenceError): verify_local_evidence(tmp_path,'remote-run-correlated','evil.json')
    with pytest.raises(RemoteEvidenceError): verify_local_evidence(tmp_path,'remote-run-correlated','../receipt.json')
    receipt['artifact']['path']='../outside.bin'
    (tmp_path/'receipt.json').write_text(json.dumps(receipt))
    with pytest.raises(RemoteEvidenceError): verify_local_evidence(tmp_path,'artifact-verified','receipt.json')


def test_lineage_readonly_local_and_remote_bound(tmp_path):
    _receipt(tmp_path)
    root=_snapshot(tmp_path)
    result=project_execution_lineage(tmp_path,RUN,receipt_paths=('receipt.json',),projection_root=root)
    assert result['journal_current'] is True
    assert result['nodes'][0]['lifecycle_evidence']=='OBSERVED_TERMINAL'
    assert result['nodes'][0]['remote']['attempt']==2
    assert result['nodes'][0]['remote']['remote_authenticated'] is False
    assert result['release_qualified'] is False
    assert result==project_execution_lineage(tmp_path,RUN,receipt_paths=('receipt.json',),projection_root=root)


def test_incomplete_journal_does_not_promote_past_terminal(tmp_path):
    root=_snapshot(tmp_path,journal=False)
    result=project_execution_lineage(tmp_path,RUN,projection_root=root)
    assert result['nodes'][0]['lifecycle_evidence']=='SNAPSHOT_ONLY'
    assert result['journal_current'] is False


def test_duplicate_terminals_and_stale_snapshot_refused(tmp_path):
    root=_snapshot(tmp_path)
    path=root/RUN/'execution-events.jsonl'
    with path.open('a') as f:
        f.write(json.dumps({'event_type':'node.finished','run_id':RUN,'sequence':3,'node_id':NODE,'status':'succeeded'})+'\n')
    with pytest.raises(LineageError,match='duplicate'):
        project_execution_lineage(tmp_path,RUN,projection_root=root)
    root=_snapshot(tmp_path)
    path=root/RUN/'execution-events.jsonl'
    path.write_text(json.dumps({'event_type':'node.finished','run_id':RUN,'sequence':1,'node_id':NODE,'status':'failed'})+'\n')
    with pytest.raises(LineageError,match='contradiction'):
        project_execution_lineage(tmp_path,RUN,projection_root=root)


def test_remote_binding_unknown_node_rejected(tmp_path):
    r=_receipt(tmp_path)
    root=_snapshot(tmp_path)
    r['exo_node_id']='other'
    (tmp_path/'receipt.json').write_text(json.dumps(r))
    with pytest.raises(LineageError,match='unknown'):
        project_execution_lineage(tmp_path,RUN,receipt_paths=('receipt.json',),projection_root=root)


def test_cli_schema_has_lineage_command():
    args=build_parser().parse_args(['evidence','lineage','--run-id',RUN,'--remote-receipt','receipt.json'])
    assert args.evidence_command=='lineage'
    assert args.remote_receipt==['receipt.json']


def test_typed_job_config_requires_only_one_receipt(tmp_path):
    base='''[project]
name = "sample"
runner = "python"
[targets.sample]
runner = "python"
[targets.sample.jobs.proof]
runner = "remote-run-correlated"
action = "verify"
inputs = ["receipt.json"]
'''
    (tmp_path/'.devtool.toml').write_text(base)
    # Direct normalized parse exercises config checks without network dispatch.
    from devtool.workflow_config import load_jobs
    from devtool.config import SUPPORTED_RUNNERS, RUNNER_OPTION_SECTIONS
    jobs=load_jobs(tmp_path, {'proof':{'runner':'remote-run-correlated','action':'verify','inputs':['receipt.json']}}, {},
                   supported_runners=SUPPORTED_RUNNERS,runner_option_sections=RUNNER_OPTION_SECTIONS)
    from devtool.models import ProjectConfig
    spec=_job_specs(ProjectConfig(repo_root=tmp_path, kind='command', runner='command',
                                   project_root=str(tmp_path)),next(iter(jobs.values())))
    assert spec[0].argv[:3][-1]=='devtool.remote_evidence_jobs'
    assert '--root' in spec[0].argv
    with pytest.raises(SystemExit):
        load_jobs(tmp_path, {'bad':{'runner':'remote-run-correlated','command':['echo','unsafe'],'inputs':['receipt.json']}}, {},
                  supported_runners=SUPPORTED_RUNNERS,runner_option_sections=RUNNER_OPTION_SECTIONS)


def test_real_multi_part_runner_emits_one_logical_terminal(tmp_path, monkeypatch):
    from devtool.models import CommandSpec, ProjectConfig
    from devtool.runners import run_specs
    from devtool.core.events import NodeFinished
    import sys
    import devtool.runners as runners
    captured = []
    monkeypatch.setattr(runners, 'publish_event', captured.append)
    config=ProjectConfig(repo_root=tmp_path, kind='command', runner='command',project_root=str(tmp_path))
    specs=[CommandSpec(name=f'part-{i}',argv=[sys.executable,'-c','print("ok")'],cwd=str(tmp_path),
             metadata={'workflow_graph':True,'workflow_node_key':NODE,'workflow_target':'api',
                       'workflow_name':'ci','workflow_node_spec_index':i,'workflow_node_spec_total':2,
                       'workflow_node_id':NODE,'workflow_ref_kind':'job'}) for i in (1,2)]
    results,_=run_specs(config,specs,verbose=False,continue_on_error=True,event_run_id=RUN)
    assert len(results)==2 and all(r.returncode==0 for r in results)
    parent=[e for e in captured if isinstance(e,NodeFinished) and e.node_id==NODE]
    parts=[e for e in captured if isinstance(e,NodeFinished) and e.node_id!=NODE]
    assert len(parent)==1, [e.node_id for e in parent]
    assert len(parts)==2
    assert parent[0].status=='succeeded'


def test_real_multi_part_failure_does_not_double_finish(tmp_path,monkeypatch):
    from devtool.models import CommandSpec, ProjectConfig
    from devtool.runners import run_specs
    from devtool.core.events import NodeFinished
    import sys
    import devtool.runners as runners
    captured=[]
    monkeypatch.setattr(runners,'publish_event',captured.append)
    config=ProjectConfig(repo_root=tmp_path,kind='command',runner='command',project_root=str(tmp_path))
    specs=[CommandSpec(name=f'part-{i}',argv=[sys.executable,'-c',f'raise SystemExit({1 if i == 1 else 0})'],
             cwd=str(tmp_path),metadata={'workflow_graph':True,'workflow_node_key':NODE,
                  'workflow_target':'api','workflow_name':'ci','workflow_node_spec_index':i,
                  'workflow_node_spec_total':2,'workflow_node_id':NODE}) for i in (1,2)]
    results,_=run_specs(config,specs,verbose=False,continue_on_error=True,event_run_id=RUN)
    assert results[0].returncode != 0
    parent=[e for e in captured if isinstance(e,NodeFinished) and e.node_id==NODE]
    from devtool.core.events import NodeStarted
    starts=[e for e in captured if isinstance(e,NodeStarted) and e.node_id==NODE]
    assert len(parent)==1 and parent[0].status=='failed'
    assert len(starts)==1, 'a skipped suffix must not restart a failed parent node'


def test_real_target_local_job_binding_and_readonly_receipt(tmp_path, monkeypatch):
    from devtool.config import load_project_config
    from devtool.workflow_runtime import build_workspace_specs
    from devtool.runners import run_specs
    import os
    _receipt(tmp_path,kind='remote-run-correlated')
    (tmp_path/'.devtool.toml').write_text('''[project]
runner = "command"
default_target = "devtool"
[targets.devtool]
runner = "command"
root = "."
[targets.devtool.jobs.proof]
runner = "remote-run-correlated"
action = "verify"
inputs = ["receipt.json"]
[targets.devtool.workflows]
verify = ["job:proof"]
''')
    cfg=load_project_config(str(tmp_path),None)
    specs=build_workspace_specs(cfg,['devtool'],'verify')
    assert len(specs)==1
    assert specs[0].metadata['workflow_canonical_identity']
    assert len(specs[0].metadata['workflow_config_fingerprint'])==64
    assert specs[0].metadata['workflow_attempt']==1
    assert specs[0].metadata['remote_dispatch_allowed'] is False
    # Make the local *verification* command import the same tested Devtool,
    # independent of whether it is pip-installed on the disposable runner.
    from pathlib import Path
    repo=Path(__file__).resolve().parents[1]
    monkeypatch.setenv('PYTHONPATH',str(repo)+os.pathsep+os.environ.get('PYTHONPATH',''))
    results,_=run_specs(cfg,specs,verbose=False,continue_on_error=True,event_run_id=RUN)
    assert len(results)==1 and results[0].returncode==0
    assert 'LOCAL_EVIDENCE_CONSISTENT' in results[0].output
    assert '"remote_authenticated": false' in results[0].output


def test_local_exo_unknown_source_refuses_correlation(tmp_path):
    _receipt(tmp_path)
    root=_snapshot(tmp_path)
    snapshot=root/RUN/'execution.json'
    doc=json.loads(snapshot.read_text())
    doc['execution']['repository_identity']={}
    snapshot.write_text(json.dumps(doc))
    with pytest.raises(LineageError,match='source unknown'):
        project_execution_lineage(tmp_path,RUN,receipt_paths=('receipt.json',),projection_root=root)
