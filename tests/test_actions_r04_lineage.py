"""R04 exact offline lineage, unknown promotion and no-write contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
from devtool.actions_lineage import lineage_snapshot, lineage_text
from devtool.github_actions import GitHubActionsError, actions_cli
from devtool.cli import build_parser

P = "a" * 32
Q = "b" * 32
SHA = "c" * 40
SLUG = "example/project"


def _fixture(tmp_path: Path, *, states=("success","success","success","success"),
             observed=True, correlated=True):
    root = tmp_path / "project";root.mkdir()
    pipeline = tmp_path / "pipelines";pipeline.mkdir()
    dispatch = tmp_path / "dispatch";dispatch.mkdir()
    raw = {"schema":1,"id":P,"name":"fixture","repository":SLUG,
           "repo_root":str(root.resolve()),"head_sha":SHA,
           "node_order":["local","dispatch","result","archive"],
           "nodes":{
            "local":{"kind":"local-invoke","state":states[0],"detail":"sensitive token 123"},
            "dispatch":{"kind":"github-dispatch","state":states[1],
                        "request_id":Q if correlated else None,"run_id":123 if correlated else None,
                        "workflow_id":42},
            "result":{"kind":"github-result","state":states[2],"run_id":123 if correlated else None},
            "archive":{"kind":"github-archive","state":states[3],"artifact_id":456}}}
    (pipeline/(P+'.json')).write_text(json.dumps(raw))
    rec={"schema":1,"request_id":Q,"repository":SLUG,"pre_dispatch_sha":SHA,
         "workflow_id":42,"run_id":123 if correlated else None,"state":"correlated"}
    if observed and correlated:
        rec["last_observation"]={"id":123,"head_sha":SHA,"run_attempt":2,
                                 "status":"completed","conclusion":"success"}
    (dispatch/(Q+'.json')).write_text(json.dumps(rec))
    return root,pipeline,dispatch,raw,rec


def _run(t):
    root, pip, dispatch, *_ = t
    return lineage_snapshot(root,SLUG,P,pipelines=pip,dispatches=dispatch)


def test_exact_receipt_and_local_rows_are_observations_not_remote_seals(tmp_path):
    f=_fixture(tmp_path); view=_run(f)
    assert [r['state'] for r in view['rows']] == ['LOCAL_OBSERVED','WAITING_REMOTE','CI_PASS_UNVERIFIED','ARCHIVE_NOT_VERIFIED']
    assert view['rows'][0]['exo_run_id'] is None
    assert view['rows'][1]['run_attempt'] == 2
    assert not any((view['remote_verified'],view['release_qualified'],view['device_qualified']))
    assert 'sensitive token' not in json.dumps(view)
    assert 'NOT QUALIFIED' in lineage_text(view, width=40)


def test_uncorrelated_post_never_invents_run_id(tmp_path):
    f=_fixture(tmp_path, states=('pending','uncertain','pending','pending'),correlated=False)
    view=_run(f)
    assert view['rows'][1]['state']=='DISPATCH_UNCERTAIN'
    assert view['rows'][1]['run_id'] is None
    assert view['rows'][2]['state']=='NOT_STARTED'


def test_no_remote_observation_does_not_promote_pipeline_success(tmp_path):
    f=_fixture(tmp_path,observed=False)
    view=_run(f)
    assert view['rows'][2]['state']=='WAITING_REMOTE'
    assert view['rows'][2]['run_attempt'] is None


@pytest.mark.parametrize('field,value', [('repository','other/repo'),('pre_dispatch_sha','d'*40),('workflow_id',17),('run_id',999)])
def test_mismatched_request_fails_closed(tmp_path,field,value):
    f=_fixture(tmp_path); rec=f[-1];rec[field]=value
    (f[2]/(Q+'.json')).write_text(json.dumps(rec))
    with pytest.raises(GitHubActionsError): _run(f)


def test_attempt_source_and_run_mismatch_fail_closed(tmp_path):
    f=_fixture(tmp_path)
    rec=f[-1];rec['last_observation']['head_sha']='e'*40
    (f[2]/(Q+'.json')).write_text(json.dumps(rec))
    with pytest.raises(GitHubActionsError,match='source'): _run(f)
    rec['last_observation']['head_sha']=SHA
    rec['last_observation']['run_attempt']=False
    (f[2]/(Q+'.json')).write_text(json.dumps(rec))
    with pytest.raises(GitHubActionsError,match='attempt'): _run(f)


def test_cross_worktree_and_journal_symlink_refused(tmp_path):
    f=_fixture(tmp_path)
    with pytest.raises(GitHubActionsError,match='worktree'):
        lineage_snapshot(tmp_path,SLUG,P,pipelines=f[1],dispatches=f[2])
    (f[1]/(P+'.json')).unlink()
    (f[1]/(P+'.json')).symlink_to(tmp_path/'target.json')
    with pytest.raises(GitHubActionsError): _run(f)


def test_registered_archive_local_bytes_are_verified_not_hosted(tmp_path):
    f=_fixture(tmp_path)
    dest=f[0]/'.devtool/artifacts/github-downloads';dest.mkdir(parents=True)
    archive=dest/'one.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('source.txt','example')
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    entry={'id':'registry123','path':'.devtool/artifacts/github-downloads/one.zip',
           'sha256':digest,'metadata':{'remote_repository':SLUG,'remote_run_id':123,
           'remote_run_attempt':2,'remote_head_sha':SHA,'remote_artifact_id':456,
           'remote_digest':'sha256:'+digest}}
    registry=f[0]/'.devtool/artifacts/registry.json'
    registry.write_text(json.dumps({'schema_version':2,'items':[entry],'verifications':[]}))
    before={str(x):x.read_bytes() for x in f[0].rglob('*') if x.is_file()}
    view=_run(f)
    node=view['rows'][3]
    assert node['state']=='ARCHIVE_LOCAL_VERIFIED'
    assert node['artifact']['sha256']==digest
    assert node['artifact']['digest_verified_locally']
    assert not node['artifact']['remote_authenticated']
    assert not view['release_qualified']
    assert before == {str(x):x.read_bytes() for x in f[0].rglob('*') if x.is_file()}
    archive.write_bytes(archive.read_bytes()+b'tamper')
    with pytest.raises(GitHubActionsError,match='digest'): _run(f)


def test_same_artifact_id_different_attempt_is_rejected(tmp_path):
    f=_fixture(tmp_path)
    dest=f[0]/'.devtool/artifacts';dest.mkdir(parents=True)
    (dest/'registry.json').write_text(json.dumps({'schema_version':2,'items':[
        {'id':'other','metadata':{'remote_artifact_id':456,'remote_repository':SLUG,
         'remote_run_id':123,'remote_run_attempt':1,'remote_head_sha':SHA}}]}))
    with pytest.raises(GitHubActionsError,match='provenance'): _run(f)


def test_missing_archive_registry_is_unknown_without_writes(tmp_path):
    f=_fixture(tmp_path); v=_run(f)
    assert v['rows'][3]['state']=='ARCHIVE_NOT_VERIFIED'
    assert not (f[0]/'.devtool').exists()


def test_preflight_failure_never_implies_ci_pass(tmp_path):
    f=_fixture(tmp_path,states=('failure','failure','pending','pending'),observed=False)
    assert _run(f)['rows'][0]['state']=='PREFLIGHT_BLOCKED'


def test_parser_and_actions_adapter_are_offline(tmp_path, monkeypatch):
    f=_fixture(tmp_path)
    parser=build_parser()
    args=parser.parse_args(['actions','lineage','--pipeline-id',P,'--github-repo',SLUG,'--json'])
    assert args.actions_command=='lineage'
    from devtool import actions_lineage
    monkeypatch.setattr(actions_lineage,'lineage_snapshot',lambda root,slug,ident: _run(f))
    data=actions_cli(args,f[0])
    assert data['phase']=='ACTIONS-R04'
    assert data['remote_dispatch'] is False


def test_nonterminal_success_observation_cannot_pass(tmp_path):
    f=_fixture(tmp_path)
    rec=f[-1];rec['last_observation']['status']='in_progress'
    (f[2]/(Q+'.json')).write_text(json.dumps(rec))
    assert _run(f)['rows'][2]['state']=='WAITING_REMOTE'
    rec['last_observation']['status']='completed'
    rec['last_observation']['run_attempt']=None
    (f[2]/(Q+'.json')).write_text(json.dumps(rec))
    assert _run(f)['rows'][2]['state']=='WAITING_REMOTE'


def test_malformed_registry_not_backed_up_or_mutated(tmp_path):
    f=_fixture(tmp_path)
    directory=f[0]/'.devtool/artifacts';directory.mkdir(parents=True)
    registry=directory/'registry.json';registry.write_text('corrupted')
    with pytest.raises(GitHubActionsError, match='unreadable'):
        _run(f)
    assert registry.read_text()=='corrupted'
    assert len(list(directory.iterdir()))==1


def test_invalid_registry_archive_path_rejected(tmp_path):
    f=_fixture(tmp_path)
    directory=f[0]/'.devtool/artifacts';directory.mkdir(parents=True)
    (directory/'registry.json').write_text(json.dumps({'schema_version':2,'items':[
        {'id':'forged','path':'../../private.zip','sha256':'0'*64,
         'metadata':{'remote_repository':SLUG,'remote_run_id':123,'remote_run_attempt':2,
                     'remote_head_sha':SHA,'remote_artifact_id':456,'remote_digest':'sha256:'+'0'*64}}]}))
    with pytest.raises(GitHubActionsError,match='escapes'):
        _run(f)
