"""NEXT-04/05-R1: source-only consumer review and zero-mutation adoption drafting."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from devtool.adoption_batch import AdoptionBlocked, draft, plan, DRAFT_KIND
from test_next05_adoption_batch import fixture, manifest, git
from test_next04_distributions import repo as consumer_repo

DEVTOOL_SHA = 'd' * 40


def spec(tmp_path, rows):
    path = tmp_path / 'draft-input.json'
    path.write_text(json.dumps({'schema':'devtool.multi-repo-adoption-input.v1','repositories':rows}))
    return path


def minimal(row, **overrides):
    item = {k:row[k] for k in ('id','path','remote','overlay')}
    item.update(overrides)
    return item


def snapshot(root):
    return (git(root,'rev-parse','HEAD'), git(root,'status','--porcelain'),
            sorted(x.name for x in root.iterdir()))


def test_readonly_source_lock_manifest_and_existing_plan_compatibility(tmp_path):
    a,ra=fixture(tmp_path,'alpha',wrapper=True)
    b,rb=fixture(tmp_path,'beta',rename=True)
    input_path=spec(tmp_path,[minimal(ra,wrapper=True),minimal(rb)])
    before=[snapshot(a),snapshot(b)]
    result=draft(input_path)
    assert result['kind']==DRAFT_KIND
    assert result['safe_to_apply'] and result['read_only']
    assert result['approval_required'] and not result['mutation_performed']
    assert result['cross_repository_atomic'] is False
    assert [r['state'] for r in result['assessments']]==['FRESH','FRESH']
    for wanted,actual in zip((ra,rb),result['manifest']['repositories']):
        for field in ('source_head','source_tree','remote_url','branch','overlay_sha256'):
            assert actual[field]==wanted[field]
    assert before==[snapshot(a),snapshot(b)]
    assert not (tmp_path/'state').exists()
    approved=tmp_path/'reviewed.json'
    approved.write_text(json.dumps(result['manifest']))
    verified=plan(approved,tmp_path/'state')
    assert verified['safe_to_apply']


def test_draft_cli_manifest_only_never_writes_or_commits(tmp_path):
    root,row=fixture(tmp_path,'alpha')
    input_path=spec(tmp_path,[minimal(row)])
    result=subprocess.run([sys.executable,'-m','devtool.adoption_batch','draft',str(input_path),'--manifest-only'],
                          text=True,capture_output=True,timeout=35)
    assert result.returncode==0,result.stderr
    obj=json.loads(result.stdout)
    assert obj['schema']=='devtool.multi-repo-adoption.v1'
    assert obj['repositories'][0]['source_head']==row['source_head']
    assert git(root,'status','--porcelain')==''
    assert not (tmp_path/'draft-input.json.json').exists()


@pytest.mark.parametrize('mutation',('dirty','detached','remote_credentials','wrong_remote','tamper_overlay'))
def test_draft_fails_closed_before_approval(tmp_path,mutation):
    root,row=fixture(tmp_path,'alpha')
    if mutation=='dirty':(root/'bad').write_text('untracked')
    if mutation=='detached':git(root,'checkout','--detach')
    if mutation=='remote_credentials':git(root,'remote','set-url','origin','https://private-token@github.com/mikey/a.git')
    if mutation=='wrong_remote':git(root,'remote','remove','origin')
    if mutation=='tamper_overlay':Path(row['overlay']).write_bytes(b'tampered')
    input_path=spec(tmp_path,[minimal(row)])
    if mutation in ('dirty','detached','remote_credentials','wrong_remote'):
        with pytest.raises(AdoptionBlocked):draft(input_path)
    else:
        result=draft(input_path)
        assert not result['safe_to_apply']
    assert git(root,'rev-parse','HEAD')==row['source_head']


def test_duplicate_names_and_nested_source_roots_rejected(tmp_path):
    root,row=fixture(tmp_path,'alpha')
    path=spec(tmp_path,[minimal(row),minimal(row)])
    with pytest.raises(AdoptionBlocked,match='duplicate'):draft(path)
    path.write_text('{"schema":"devtool.multi-repo-adoption-input.v1","schema":"fake","repositories":[]}')
    with pytest.raises(AdoptionBlocked,match='duplicate JSON key'):draft(path)


def test_no_unsafe_assumed_approval_for_unreviewed_distribution(tmp_path):
    root,row=fixture(tmp_path,'alpha')
    inp=spec(tmp_path,[minimal(row,distribution={'target':'app','workflow':'ci','devtool_sha':DEVTOOL_SHA})])
    with pytest.raises(AdoptionBlocked,match='distribution source review blocked'):draft(inp)
    assert git(root,'status','--porcelain')==''


def _consumer_overlay(tmp_path,root):
    import zipfile
    original=(root/'.devtool.toml').read_bytes()
    new=original+b'\n# adoption-reviewed\n'
    payload={'schema_version':2,'type':'overlay','name':'consumer-adopt',
             'inventory':{'format':1,'entries':[{'path':'.devtool.toml','action':'modify',
                  'sha256':hashlib.sha256(new).hexdigest(),'size':len(new),
                  'source_sha256':hashlib.sha256(original).hexdigest()}]},
             'validation':{'required':False},'base_contract_mode':'touched-files',
             'base_commit':git(root,'rev-parse','HEAD'),'base_tree':git(root,'rev-parse','HEAD^{tree}'),
             'touched_paths':['.devtool.toml'],
             'source_sha256':{'.devtool.toml':hashlib.sha256(original).hexdigest()},
             'expected_post_sha256':{'.devtool.toml':hashlib.sha256(new).hexdigest()},
             'apply':{'requires_clean_worktree':True,'commit':{'enabled':True,'mode':'new','strategy':'single','message':'adopt'}}}
    zip_path=tmp_path/'consumer.zip'
    with zipfile.ZipFile(zip_path,'w') as z:
        z.writestr('.devtool.toml',new)
        z.writestr('.devtool-artifact.json',json.dumps(payload))
    return zip_path


def test_go_consumer_source_review_does_not_promote_artifacts_or_run_hosted(tmp_path):
    root=consumer_repo(tmp_path)
    git(root,'remote','add','origin','https://example.org/repo/go-consumer.git')
    path=_consumer_overlay(tmp_path,root)
    inp=spec(tmp_path,[{'id':'goconsumer','path':str(root),'remote':'origin','overlay':str(path),
                       'distribution':{'target':'app','workflow':'ci','devtool_sha':DEVTOOL_SHA}}])
    original=snapshot(root)
    result=draft(inp)
    assert result['safe_to_apply'], result['assessments']
    review=result['consumer_source_reviews'][0]
    assert review['source_commit']==git(root,'rev-parse','HEAD')
    assert review['distribution_ids']==['hello']
    assert len(review['contract_sha256'])==64
    assert not review['hosted_execution_verified']
    assert not review['local_bytes_verified']
    assert not review['consumer_qualified']
    assert snapshot(root)==original


def test_python_consumer_source_review_reuses_provider_and_does_not_install(tmp_path):
    root=consumer_repo(tmp_path,kind='python')
    git(root,'remote','add','origin','https://example.org/repo/py-consumer.git')
    path=_consumer_overlay(tmp_path,root)
    inp=spec(tmp_path,[{'id':'pyconsumer','path':str(root),'remote':'origin','overlay':str(path),
                       'distribution':{'target':'app','workflow':'ci','devtool_sha':DEVTOOL_SHA}}])
    before=snapshot(root)
    result=draft(inp)
    assert result['safe_to_apply'],result['assessments']
    review=result['consumer_source_reviews'][0]
    assert review['distribution_ids']==['wheel']
    assert len(review['source_plan_sha256'])==64
    assert not any(review[field] for field in ('local_bytes_verified','hosted_execution_verified',
                                              'consumer_qualified','release_qualified'))
    assert snapshot(root)==before


def test_top_level_cli_draft_dispatch_matches_review_contract(tmp_path):
    root,row=fixture(tmp_path,'alpha')
    inp=spec(tmp_path,[minimal(row)])
    command=[sys.executable,'-m','devtool','--no-hud','-r',str(root),
             'adopt-batch','draft',str(inp),'--json']
    proc=subprocess.run(command,capture_output=True,text=True,timeout=40)
    assert proc.returncode==0,proc.stderr[-800:]
    payload=json.loads(proc.stdout)
    assert payload['data']['kind']==DRAFT_KIND and payload['data']['safe_to_apply'], (proc.stdout[-1400:], proc.stderr[-500:])
    assert payload['data']['manifest']['repositories'][0]['source_head']==row['source_head']
    assert not any(x.startswith('.adoption') for x in os.listdir(root))
