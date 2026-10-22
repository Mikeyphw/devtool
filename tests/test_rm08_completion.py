"""RM-08 closure behavior, including positive execution and adversarial refusals."""
from __future__ import annotations
import os
import subprocess
from pathlib import Path
import pytest
from test_rm08_projects import repo,plan,commit,add_python_project,git,PIN
from devtool.actions_language_providers import ProviderError,_workflow_yaml
from devtool.actions_graph_export import compile_graph,workflow_yaml
from devtool.actions_rm08_projects import (go_workspace_manifest,python_project_manifest,
    verify_go_repeat_build,ProjectReviewError,smoke_sdist)


def test_go_bounded_real_version_matrix_and_isolated_jobs(tmp_path):
    root=repo(tmp_path)
    path=root/'.devtool.toml'
    path.write_text(path.read_text().replace('inputs = ["services/api/go.mod"]',
        'inputs = ["services/api/go.mod"]\nmetadata = {ci_go_versions="1.23,1.24"}'))
    commit(root)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    assert len(graph['jobs'])==2
    assert {j['toolchain_version'] for j in graph['jobs']}=={'1.23','1.24'}
    assert len({j['matrix_cell'] for j in graph['jobs']})==2
    assert len({j['id'] for j in graph['jobs']})==2
    yaml=workflow_yaml(graph)
    assert "go-version: '1.23'" in yaml and "go-version: '1.24'" in yaml
    with pytest.raises(ProviderError, match='multi-job'):_workflow_yaml(plan(root))
    path.write_text(path.read_text().replace('1.23,1.24','1.22,1.23'))
    with pytest.raises(ProviderError, match='predates'):plan(root)
    path.write_text(path.read_text().replace('1.22,1.23','1.23,1.23'))
    with pytest.raises(ProviderError, match='unique'):plan(root)


def test_python_three_way_version_matrix_multi_os_and_bounds(tmp_path):
    root=repo(tmp_path,python=True)
    path=root/'.devtool.toml'
    path.write_text(path.read_text().replace('command = ["python3", "-m", "unittest"]',
          'command = ["python3", "-m", "unittest"]\nmetadata = {ci_python_versions="3.11,3.12,3.13"}'))
    commit(root)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,
                        host_profiles='ubuntu-24.04-x64,macos-15-intel')
    assert len(graph['jobs'])==6
    assert len({j['matrix_cell'] for j in graph['jobs']})==6
    assert {j['runner'] for j in graph['jobs']}=={'ubuntu-24.04','macos-15-intel'}
    content=workflow_yaml(graph)
    for version in ('3.11','3.12','3.13'):
        assert f"python-version: '{version}'" in content
    path.write_text(path.read_text().replace('3.11,3.12,3.13','3.11,3.12,3.13,3.14'))
    with pytest.raises(ProviderError,match='unique|1..3'):plan(root)


def test_local_go_workspace_requirements_replacements_and_cycles(tmp_path):
    root=repo(tmp_path)
    lib=root/'services/api/lib';lib.mkdir()
    (lib/'go.mod').write_text('module example.org/lib\ngo 1.23\nrequire example.org/api v1.0.0\n')
    (root/'services/api/go.mod').write_text('module example.org/api\ngo 1.23\n')
    (root/'go.work').write_text('go 1.23\nuse (\n ./services/api\n ./services/api/lib\n)\n')
    commit(root)
    workspace=go_workspace_manifest(root,directory='services/api/lib')
    assert workspace['workspace_edges']['services/api/lib']==['services/api']
    assert workspace['workspace_build_order'].index('services/api')<workspace['workspace_build_order'].index('services/api/lib')
    (root/'services/api/go.mod').write_text('module example.org/api\ngo 1.23\nrequire example.org/lib v1.0.0\n')
    with pytest.raises(ProjectReviewError,match='Cyclic'):go_workspace_manifest(root,directory='services/api/lib')
    (root/'services/api/go.mod').write_text('module example.org/api\ngo 1.23\n')
    (root/'go.work').write_text('go 1.23\nuse (\n ./services/api\n ./services/api/lib\n)\nreplace example.org/missing => ../../escape\n')
    with pytest.raises(ProjectReviewError,match='repository|replace'):go_workspace_manifest(root,directory='services/api/lib')


@pytest.mark.parametrize('manager,lock',[
 ('uv','uv.lock'),('poetry','poetry.lock'),('pdm','pdm.lock')])
def test_reviewed_manager_export_hash_binding_and_drift(tmp_path,manager,lock):
    root=repo(tmp_path,python=True);add_python_project(root)
    (root/'requirements.txt').unlink()
    (root/lock).write_text('[[package]]\nname="setuptools"\nversion="75.1.0"\n'
       'files=[{file="setuptools.whl",hash="sha256:'+'a'*64+'"}]\n')
    (root/'requirements-ci.txt').write_text('setuptools==75.1.0 --hash=sha256:'+'a'*64+'\n')
    commit(root)
    declaration=python_project_manifest(root,require_package=True)
    assert declaration['manager']==manager
    assert declaration['install_lock']=='requirements-ci.txt'
    assert declaration['dependency_install_supported']
    (root/'requirements-ci.txt').write_text('setuptools==75.1.1 --hash=sha256:'+'a'*64+'\n')
    with pytest.raises(ProjectReviewError,match='does not match'):
        python_project_manifest(root,require_package=True)
    (root/'requirements-ci.txt').write_text('setuptools==75.1.0 --hash=sha256:'+'a'*64+'\n')
    (root/lock).write_text('[[package]]\nname="setuptools"\nversion="75.1.0"\n'
       'files=[{file="setuptools.whl",hash="untrusted"}]\n')
    with pytest.raises(ProjectReviewError,match='SHA-256'):
        python_project_manifest(root,require_package=True)


def test_native_owned_coverage_command_and_report_output(tmp_path):
    root=repo(tmp_path,python=True)
    cfg=root/'.devtool.toml'
    cfg.write_text(cfg.read_text().replace('"unittest"','"coverage", "xml", "-o", "dist/coverage.xml"'))
    with pytest.raises(ProviderError,match='coverage'):
        compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    cfg.write_text(cfg.read_text().replace('command = ["python3", "-m", "coverage", "xml", "-o", "dist/coverage.xml"]',
                   'command = ["python3", "-m", "coverage", "xml", "-o", "dist/coverage.xml"]\noutputs = ["dist/coverage.xml"]'))
    commit(root)
    job=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)['jobs'][0]
    assert job['coverage_report'] and job['outputs']==['dist/coverage.xml']
    cfg.write_text(cfg.read_text().replace('"xml", "-o", "dist/coverage.xml"','"erase"'))
    with pytest.raises(ProviderError,match='coverage'):plan(root)


def test_go_repeat_build_executes_twice_and_detects_modified_candidate(tmp_path):
    root=repo(tmp_path)
    module=root/'services/api';path=module/'dist/demo';path.parent.mkdir()
    argv=['go','-C','services/api','build','-trimpath','-buildvcs=false','-o','dist/demo','.']
    env={'GOOS':'linux','GOARCH':'amd64','CGO_ENABLED':'0'}
    command=subprocess.run(argv,cwd=root,env={**os.environ,**env,'GOTOOLCHAIN':'local'},
                           capture_output=True,timeout=90)
    assert command.returncode==0,command.stderr.decode(errors='replace')
    evidence=verify_go_repeat_build(root,argv,env)
    assert len(evidence['independent_rebuild_sha256'])==2 and evidence['matching']
    assert evidence['independent_rebuild_sha256']==[evidence['candidate_sha256']]*2
    path.write_bytes(path.read_bytes()+b'altered')
    with pytest.raises(ProjectReviewError,match='differs'):
        verify_go_repeat_build(root,argv,env)


def test_untrusted_sdist_smoke_paths_refused(tmp_path):
    root=repo(tmp_path,python=True);add_python_project(root);commit(root)
    with pytest.raises(ProjectReviewError,match='[Ss]dist'):
        smoke_sdist(root,'../../escape.tar.gz',name='demo-pkg',version='1.0',entrypoint='demo')


def test_real_offline_sdist_build_install_with_hash_pinned_backend(tmp_path):
    """Real PEP 517 install, no network, from synthetic reviewed wheelhouse."""
    import io,tarfile,zipfile,csv,base64,hashlib
    root=repo(tmp_path,python=True)
    house=root/'dist/wheelhouse';house.mkdir(parents=True)
    backend_code='''import io,csv,zipfile,base64,hashlib

def get_requires_for_build_wheel(config_settings=None):return []

def build_wheel(wheel_directory,config_settings=None,metadata_directory=None):
    from pathlib import Path
    info="demo_pkg-1.0.dist-info"
    files={"demo.py":b"def main():\\n    print('hello')\\n    return 0\\n",
           info+"/METADATA":b"Metadata-Version: 2.1\\nName: demo-pkg\\nVersion: 1.0\\n",
           info+"/WHEEL":b"Wheel-Version: 1.0\\nGenerator: local-fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n",
           info+"/entry_points.txt":b"[console_scripts]\\ndemo = demo:main\\n"}
    records=[]
    for name,data in files.items():
        token=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        records.append([name,"sha256="+token,str(len(data))])
    records.append([info+"/RECORD","",""])
    buf=io.StringIO();csv.writer(buf,lineterminator="\\n").writerows(records)
    files[info+"/RECORD"]=buf.getvalue().encode()
    name="demo_pkg-1.0-py3-none-any.whl"
    with zipfile.ZipFile(str(Path(wheel_directory)/name),'w') as archive:
        for k,v in files.items():archive.writestr(k,v)
    return name
'''
    def make_wheel(name,version,files):
        dist=name.replace('-','_')+'-'+version+'.dist-info'
        files={**files, dist+'/METADATA':f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n'.encode(),
               dist+'/WHEEL':b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
        records=[]
        for key,val in files.items():
            digest=base64.urlsafe_b64encode(hashlib.sha256(val).digest()).rstrip(b'=').decode()
            records.append([key,'sha256='+digest,str(len(val))])
        records.append([dist+'/RECORD','',''])
        b=io.StringIO();csv.writer(b,lineterminator='\n').writerows(records)
        files[dist+'/RECORD']=b.getvalue().encode()
        filename=name.replace('-','_')+'-'+version+'-py3-none-any.whl'
        output=house/filename
        with zipfile.ZipFile(output,'w') as z:
            for key,data in files.items():z.writestr(key,data)
        return hashlib.sha256(output.read_bytes()).hexdigest()
    builder_sha=make_wheel('flit-core','1.0',{'flit_core/__init__.py':b'',
                                             'flit_core/buildapi.py':backend_code.encode()})
    build_sha=make_wheel('build','1.2.2',{'build/__init__.py':b''})
    (root/'pyproject.toml').write_text('[project]\nname="demo-pkg"\nversion="1.0"\n'
        '[project.scripts]\ndemo="demo:main"\n[build-system]\n'
        'requires=["flit-core==1.0"]\nbuild-backend="flit_core.buildapi"\n')
    (root/'requirements.txt').write_text('--require-hashes\nbuild==1.2.2 --hash=sha256:'+build_sha+
                                         '\nflit-core==1.0 --hash=sha256:'+builder_sha+'\n')
    members={'demo_pkg-1.0/PKG-INFO':b'Metadata-Version: 2.1\nName: demo-pkg\nVersion: 1.0\n',
             'demo_pkg-1.0/demo.py':b'def main():\n    print("hello")\n    return 0\n',
             'demo_pkg-1.0/pyproject.toml':(root/'pyproject.toml').read_bytes()}
    archive=root/'dist/demo_pkg-1.0.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name,raw in members.items():
            header=tarfile.TarInfo(name);header.size=len(raw)
            tar.addfile(header,io.BytesIO(raw))
    commit(root)
    result=smoke_sdist(root,'dist/demo_pkg-1.0.tar.gz',name='demo-pkg',version='1.0',entrypoint='demo')
    assert result['installed_without_network'] and result['invoked_exit_zero'] and not result['release_qualified']
    (house/'build-1.2.2-py3-none-any.whl').write_bytes(b'tampered')
    with pytest.raises(ProjectReviewError,match='backend provisioning'):
        smoke_sdist(root,'dist/demo_pkg-1.0.tar.gz',name='demo-pkg',version='1.0',entrypoint='demo')


def test_automatic_coverage_is_in_native_job_and_uploaded_with_handoff(tmp_path, monkeypatch):
    """Actual pytest is instrumented, XML verified and staged by its DAG owner."""
    from devtool.actions_graph_export import run_node
    root=repo(tmp_path,python=True)
    add_python_project(root)
    lock=root/'requirements.txt'
    lock.write_text(lock.read_text()+'coverage==7.6.1 --hash=sha256:'+'e'*64+'\n')
    project=root/'tests';project.mkdir()
    (project/'test_sample.py').write_text('def test_covered():\n    assert (1 + 1)==2\n')
    cfg=root/'.devtool.toml'
    cfg.write_text(cfg.read_text().replace('command = ["python3", "-m", "unittest"]',
        'command = ["python3", "-m", "pytest", "-q", "tests/test_sample.py"]\n'
        'outputs = ["dist/coverage.xml"]\n'
        'metadata = {ci_python_policy="reviewed", ci_python_auto_coverage=true}'))
    commit(root)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    assert graph['jobs'][0]['auto_coverage'] and graph['jobs'][0]['coverage_report']
    with pytest.raises(ProviderError,match='multi-job'):_workflow_yaml(plan(root))
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    monkeypatch.setenv('XDG_CACHE_HOME',str(tmp_path/'cache'))
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','42061')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    monkeypatch.setenv('PYTHONPATH',str(root))
    result=run_node(root,target='app',workflow='ci',devtool_sha=PIN,
       graph_sha=graph['graph_sha256'],job_id=graph['jobs'][0]['id'],
       inputs=tmp_path/'none',outputs=tmp_path/'outputs')
    assert result['status']=='LOCAL_JOB_SUCCEEDED'
    assert result['coverage'][0]['shards']
    assert (root/'dist/coverage.xml').is_file()
    assert (tmp_path/'outputs'/graph['jobs'][0]['id']/'payload/dist/coverage.xml').is_file()
    assert not (root/'dist/.coverage').exists()
    with pytest.raises(ProviderError,match='exists before'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,
           graph_sha=graph['graph_sha256'],job_id=graph['jobs'][0]['id'],
           inputs=tmp_path/'none',outputs=tmp_path/'other')


def test_automatic_coverage_bad_claims_fail_closed(tmp_path):
    root=repo(tmp_path,python=True);add_python_project(root)
    cfg=root/'.devtool.toml'
    cfg.write_text(cfg.read_text().replace('command = ["python3", "-m", "unittest"]',
       'command = ["python3", "-m", "pytest"]\n'
       'metadata = {ci_python_auto_coverage=true, ci_python_policy="reviewed"}'))
    git(root,'add','.')
    with pytest.raises(ProviderError,match='output'):plan(root)
    cfg.write_text(cfg.read_text().replace('metadata =', 'outputs = ["dist/coverage.xml"]\nmetadata ='))
    with pytest.raises(ProviderError,match='coverage dependency'):plan(root)
    req=root/'requirements.txt';req.write_text(req.read_text()+'# coverage==8.0.0 --hash=sha256:'+'c'*64+'\n')
    with pytest.raises(ProviderError,match='coverage dependency'):plan(root)
    req.write_text(req.read_text()+'coverage==7.6.1 --hash=sha256:'+'d'*64+'\n')
    assert plan(root)['nodes'][0]['auto_coverage']
    cfg.write_text(cfg.read_text().replace('"pytest"','"unittest"'))
    with pytest.raises(ProviderError,match='requires a native'):plan(root)
