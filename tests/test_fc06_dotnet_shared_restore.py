"""FC-06: same-job NuGet sharing cannot be inferred from independent DAG jobs."""
from __future__ import annotations
import base64
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from devtool.actions_rm09_dotnet import (DotNetError,inspect_dotnet_job,
    solution_graph,review_shared_restore,verify_shared_restore_store)
from devtool.actions_graph_export import compile_graph,run_node
from devtool.actions_language_providers import ProviderError
from tests.test_rm09_dotnet import dotnet_repo,git
from tests.test_next03_language_providers import PIN

BLOBS={'A':b'A','B':b'B','C':b'C'}
HASH_A=base64.b64encode(hashlib.sha512(BLOBS['A']).digest()).decode()
HASH_B=base64.b64encode(hashlib.sha512(BLOBS['B']).digest()).decode()
HASH_C=base64.b64encode(hashlib.sha512(BLOBS['C']).digest()).decode()


def make_repo(tmp_path, *, shared=True):
    root=dotnet_repo(tmp_path)
    (root/'Lib.slnx').write_text('<Solution><Project Path="Lib/Lib.csproj" /></Solution>')
    for project in ('Lib','App'):
        items={
          'Common.Package':{'type':'Direct','resolved':'1.0.0','contentHash':HASH_A},
          'Different.Package':{'type':'Transitive','resolved':'2.0.0' if project=='Lib' else '1.0.0',
                               'contentHash':HASH_B if project=='Lib' else HASH_C}}
        if not shared and project=='App':items.pop('Common.Package')
        (root/project/'packages.lock.json').write_text(json.dumps({'version':1,'dependencies':{'net8.0':items}}))
    cfg=root/'.devtool.toml'
    cfg.write_text(cfg.read_text().replace('[targets.app.workflows]','''[targets.app.jobs.build.metadata]
ci_dotnet_shared_solutions = ["All.sln", "Lib.slnx"]
[targets.app.workflows]'''))
    git(root,'add','.');git(root,'-c','user.name=Test','-c','user.email=test@example.org','commit','-qm','FC06 two solutions')
    return root


def reviewed(root):
    return inspect_dotnet_job(root,['dotnet','build','All.sln','--configuration','Release','--no-restore'],
        {'ci_dotnet_shared_solutions':['All.sln','Lib.slnx']})['shared_restore']


def materialize(store,plan):
    for pkg in plan['packages']:
        p=store/pkg['id']/pkg['version']/(pkg['id']+'.'+pkg['version']+'.nupkg.sha512')
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(pkg['sha512']+'\n')
        blob=next(v for v in BLOBS.values() if base64.b64encode(hashlib.sha512(v).digest()).decode()==pkg['sha512'])
        p.with_suffix('').write_bytes(blob)


def test_review_binds_two_solutions_one_shared_identity_and_conflicting_versions(tmp_path):
    root=make_repo(tmp_path)
    plan=reviewed(root)
    assert ['common.package','1.0.0'] in plan['shared_package_keys']
    assert len(plan['packages'])==3
    assert not plan['runtime_verified'] and not plan['cross_job_reuse']
    assert plan['sdk_version']=='8.0.416'
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    build=next(j for j in graph['jobs'] if j['dotnet_command']=='build')
    assert build['dotnet_shared_restore']['review_sha256']==plan['review_sha256']
    assert 'NUGET_PACKAGES:' in __import__('devtool.actions_graph_export',fromlist=['workflow_yaml']).workflow_yaml(graph)


def test_review_refuses_unshared_or_unreviewed_and_mismatched_source(tmp_path):
    root=make_repo(tmp_path,shared=False)
    project=root/'App/App.csproj'
    project.write_text(project.read_text().replace('<ItemGroup><ProjectReference Include="../Lib/Lib.csproj" /></ItemGroup>',''))
    (root/'App.slnx').write_text('<Solution><Project Path="App/App.csproj" /></Solution>')
    git(root,'add','App.slnx')
    graph=solution_graph(root,selected='App.slnx')
    with pytest.raises(DotNetError,match='genuinely shared'):
        review_shared_restore(root,graph,'App.slnx',['App.slnx','Lib.slnx'])
    with pytest.raises(DotNetError,match='2–8 distinct'):
        review_shared_restore(root,solution_graph(root,selected='All.sln'),'All.sln',['All.sln','All.sln'])
    with pytest.raises(DotNetError,match='reviewed solutions'):
        review_shared_restore(root,solution_graph(root,selected='All.sln'),'All.sln',['All.sln','unknown.sln'])


def test_review_rejects_same_package_key_conflicting_content_hash(tmp_path):
    root=make_repo(tmp_path)
    lock=root/'App/packages.lock.json';data=json.loads(lock.read_text())
    data['dependencies']['net8.0']['Common.Package']['contentHash']=HASH_B
    lock.write_text(json.dumps(data))
    with pytest.raises(DotNetError,match='conflicting NuGet content hashes'):reviewed(root)


def test_review_refuses_windows_only_shared_solution_on_linux(tmp_path):
    root=make_repo(tmp_path)
    project=root/'App/App.csproj';project.write_text(project.read_text().replace('net8.0','net8.0-windows'))
    with pytest.raises(DotNetError,match='Windows-only'):reviewed(root)


def test_store_verification_requires_matching_locked_package_bytes(tmp_path):
    root=make_repo(tmp_path);plan=reviewed(root)
    store=tmp_path/'store';store.mkdir();materialize(store,plan)
    receipt=verify_shared_restore_store(root,plan,store,sdk_version='8.0.416',restored_solutions=plan['solutions'])
    assert receipt['verified_package_count']==3
    assert all('archive_sha256' in p for p in receipt['verified_packages'])
    assert receipt['verified_shared_package_count']>=1
    assert receipt['same_job_store'] and not receipt['authenticated_host']
    with pytest.raises(DotNetError,match='SDK'):
        verify_shared_restore_store(root,plan,store,sdk_version='8.0.999',restored_solutions=plan['solutions'])
    p=store/'common.package'/'1.0.0'/'common.package.1.0.0.nupkg.sha512'
    p.write_text(HASH_C)
    with pytest.raises(DotNetError,match='SHA-512'):
        verify_shared_restore_store(root,plan,store,sdk_version='8.0.416',restored_solutions=plan['solutions'])
    p.write_text(HASH_A)
    archive=p.with_suffix('')
    archive.write_bytes(b'corrupted')
    with pytest.raises(DotNetError,match='archive bytes'):
        verify_shared_restore_store(root,plan,store,sdk_version='8.0.416',restored_solutions=plan['solutions'])
    archive.write_bytes(BLOBS['A'])
    (root/'Lib/packages.lock.json').write_text('{}')
    with pytest.raises(DotNetError,match='source/lock changed'):
        verify_shared_restore_store(root,plan,store,sdk_version='8.0.416',restored_solutions=plan['solutions'])


def test_store_refuses_symlinked_package_metadata(tmp_path):
    root=make_repo(tmp_path);plan=reviewed(root)
    store=tmp_path/'store';store.mkdir();materialize(store,plan)
    p=store/'common.package'/'1.0.0'/'common.package.1.0.0.nupkg.sha512'
    p.unlink();p.symlink_to(tmp_path/'outside')
    with pytest.raises(DotNetError,match='symlinked'):
        verify_shared_restore_store(root,plan,store,sdk_version='8.0.416',restored_solutions=plan['solutions'])


def test_native_node_uses_one_isolated_store_and_emits_actual_receipt(tmp_path,monkeypatch):
    from devtool import runners
    import devtool.actions_graph_export as export
    root=make_repo(tmp_path)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    job=next(j for j in graph['jobs'] if j['dotnet_command']=='build')
    plan=job['dotnet_shared_restore']
    assert plan
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','20261025');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    real_run=subprocess.run
    def sdk_run(cmd,*a,**kw):
        if cmd==['dotnet','--version']:return SimpleNamespace(returncode=0,stdout='8.0.416\n',stderr='')
        return real_run(cmd,*a,**kw)
    monkeypatch.setattr(export.__import__('subprocess') if False else subprocess,'run',sdk_run)
    observed=[]
    def direct(cfg,specs,**kw):
        spec=specs[0]; observed.append((list(spec.argv),spec.env.get('NUGET_PACKAGES')))
        assert spec.env['NUGET_PACKAGES']
        store=Path(spec.env['NUGET_PACKAGES']);assert store.is_dir()
        if spec.argv[:2]==['dotnet','restore']:
            materialize(store,plan)
        return [SimpleNamespace(returncode=0,skipped=False)],''
    monkeypatch.setattr(runners,'run_specs',direct)
    result=run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                    job_id=job['id'],inputs=tmp_path/'input',outputs=tmp_path/'out')
    assert result['status']=='LOCAL_JOB_SUCCEEDED'
    assert [x[0][:3] for x in observed]==[
      ['dotnet','restore','All.sln'],['dotnet','restore','Lib.slnx'],['dotnet','build','All.sln']]
    assert len(set(x[1] for x in observed))==1
    receipt=next(x for x in result['node_receipts'] if x['kind']=='devtool.rm09-native-shared-restore-evidence')
    assert receipt['review_sha256']==plan['review_sha256']
    assert receipt['verified_package_count']==3


def test_failed_shared_restore_never_runs_build_or_emits_receipt(tmp_path,monkeypatch):
    from devtool import runners
    import devtool.actions_graph_export as export
    root=make_repo(tmp_path)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    job=next(j for j in graph['jobs'] if j['dotnet_command']=='build')
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','500');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    real_run=subprocess.run
    monkeypatch.setattr(subprocess,'run',lambda cmd,*a,**kw: SimpleNamespace(returncode=0,stdout='8.0.416\n') if cmd==['dotnet','--version'] else real_run(cmd,*a,**kw))
    observed=[]
    def bad(cfg,specs,**kw):
        observed.append(specs[0].argv)
        return [SimpleNamespace(returncode=1,skipped=False)],''
    monkeypatch.setattr(runners,'run_specs',bad)
    with pytest.raises(ProviderError,match='shared locked restore failed'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                 job_id=job['id'],inputs=tmp_path/'input',outputs=tmp_path/'out')
    assert len(observed)==1 and observed[0][:2]==['dotnet','restore']
