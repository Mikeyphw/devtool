"""S7 P02 public CLI, safety and lightweight consumer acceptance."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from devtool.cli import build_parser
from devtool.delivery_cli import run_adoption_command


def git(repo,*args):
    return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True).stdout.strip()


@pytest.fixture
def lightweight(tmp_path):
    repo=tmp_path/'consumer';repo.mkdir()
    git(repo,'init','-b','main');git(repo,'config','user.name','CI')
    git(repo,'config','user.email','ci@example.invalid')
    git(repo,'remote','add','origin','git@github.com:example/lightweight.git')
    (repo/'README.md').write_text('Original\n')
    scripts=repo/'scripts';scripts.mkdir()
    (scripts/'old.sh').write_text('gh run watch 123\n')
    git(repo,'add','.');git(repo,'commit','-m','baseline')
    payload=tmp_path/'payload';payload.mkdir()
    (payload/'README.md').write_text('New\n')
    (payload/'docs').mkdir();(payload/'docs'/'migration.md').write_text('Pilot\n')
    plan=tmp_path/'plan.json'
    plan.write_text(json.dumps({'schema_version':1,'changes':[
        {'path':'README.md','action':'modify'}, {'path':'docs/migration.md','action':'add'}]}))
    return repo,payload,plan,tmp_path/'pilot.zip'


def test_schema_has_canonical_nested_s7_commands():
    p=build_parser()
    a=p.parse_args(['--repo','/tmp/example','delivery','adoption','audit'])
    assert (a.command,a.delivery_command,a.delivery_adoption_command)==('delivery','adoption','audit')
    b=p.parse_args(['delivery','adoption','scaffold','--plan','a.json','--payload','payload',
                    '--output','out.zip','--expected-head','a'*40])
    assert b.expected_head=='a'*40 and b.plan=='a.json' and b.output=='out.zip'
    with pytest.raises(SystemExit):
        p.parse_args(['delivery','adoption','scaffold','--plan','a.json'])


def test_canonical_audit_read_only_and_no_green_claims(lightweight,capsys):
    repo,*_=lightweight;head=git(repo,'rev-parse','HEAD')
    a=build_parser().parse_args(['--repo',str(repo),'delivery','adoption','audit','--json'])
    assert run_adoption_command(a)==0
    obj=json.loads(capsys.readouterr().out)
    assert obj['source_sha']==head and obj['candidate_duplicate_launchers']==['scripts/old.sh']
    assert obj['ci_qualified'] is False and obj['release_qualified'] is False
    assert git(repo,'status','--porcelain')==''


def test_cli_scaffold_real_consumer_fixture_and_provenance(lightweight,capsys):
    repo,payload,plan,out=lightweight;head=git(repo,'rev-parse','HEAD')
    args=['--repo',str(repo),'delivery','adoption','scaffold',
          '--payload',str(payload),'--plan',str(plan),'--output',str(out),
          '--expected-head',head,'--json']
    assert run_adoption_command(build_parser().parse_args(args))==0
    receipt=json.loads(capsys.readouterr().out)
    assert receipt['source_sha']==head and receipt['applied'] is False
    assert receipt['remote_mutations'] is False and receipt['ci_qualified'] is False
    with zipfile.ZipFile(out) as z:
        manifest=json.loads(z.read('.devtool-artifact.json'))
    assert manifest['base_commit']==head
    assert manifest['base_tree']==git(repo,'rev-parse','HEAD^{tree}')
    assert hashlib.sha256(out.read_bytes()).hexdigest()==receipt['sha256']
    assert git(repo,'status','--porcelain')==''
    assert (repo/'README.md').read_text()=='Original\n'
    assert not (repo/'docs'/'migration.md').exists()


def test_native_cli_entrypoint_end_to_end(lightweight):
    repo,*_=lightweight
    result=subprocess.run([sys.executable,'-m','devtool','--repo',str(repo),
                           'delivery','adoption','audit','--json'],
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    obj=json.loads(result.stdout)
    assert obj['worktree_clean'] and not obj['release_qualified']
    assert git(repo,'status','--porcelain')==''
    assert not (repo/'.devtool').exists(), 'read-only CLI must not initialize consumer state'



def test_native_scaffold_entrypoint_creates_only_artifact(lightweight):
    repo,payload,plan,out=lightweight
    head=git(repo,'rev-parse','HEAD')
    cmd=[sys.executable,'-m','devtool','--repo',str(repo),'delivery','adoption','scaffold',
         '--payload',str(payload),'--plan',str(plan),'--output',str(out),
         '--expected-head',head,'--json']
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
    assert p.returncode==0,p.stderr
    receipt=json.loads(p.stdout)
    assert receipt['source_sha']==head and not receipt['applied']
    assert git(repo,'status','--porcelain')==''
    assert not (repo/'.devtool').exists()
    assert out.exists()


def test_new_cli_fail_closed_on_source_drift_and_no_artifact(lightweight,capsys):
    repo,payload,plan,out=lightweight
    a=build_parser().parse_args(['--repo',str(repo),'delivery','adoption','scaffold',
        '--payload',str(payload),'--plan',str(plan),'--output',str(out),'--expected-head','f'*40])
    assert run_adoption_command(a)==2
    assert 'Expected source SHA differs' in capsys.readouterr().err
    assert not out.exists() and git(repo,'status','--porcelain')==''


def test_no_implicit_mutation_when_cli_uses_unrecognized_subcommand(capsys):
    # Selection must be resolved by schema, not an unknown action falling
    # into the existing delivery-plan branch.
    with pytest.raises(SystemExit):
        build_parser().parse_args(['delivery','adoption','push'])
