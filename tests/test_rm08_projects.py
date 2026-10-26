"""RM-08 behavioral promises: actual manifest selection, job pinning, adversarial policies."""
from __future__ import annotations
import base64
import csv
import hashlib
import io
import subprocess
import zipfile
from pathlib import Path
import pytest

from devtool.actions_language_providers import ProviderError, provider_plan, _workflow_yaml
from devtool.actions_graph_export import compile_graph, workflow_yaml
from devtool.actions_rm08_projects import (go_workspace_manifest, python_project_manifest,
                                           smoke_wheel, ProjectReviewError)

PIN='d'*40

@pytest.fixture(autouse=True)
def _isolate_disposable_repo_from_hosted_github_sha(monkeypatch):
    # The fixture repositories have their own, explicitly checked-out HEAD.
    # The Actions host checkout SHA is unrelated and must not be inherited.
    # Production source-pin checks remain enforced for each disposable repo.
    monkeypatch.delenv("GITHUB_SHA", raising=False)



def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],check=True,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,text=True).stdout.strip()


def repo(tmp_path, *, nested=True, python=False):
    root=tmp_path/'repo';root.mkdir();git(root,'init','-q')
    base=['[project]','runner = "python"','[workspace]','default_target = "app"',
          '[wrapper]','schema = 2','[targets.app]','runner = "python"','root = "."']
    if not python:
        module=root/'services/api' if nested else root
        module.mkdir(parents=True,exist_ok=True) if nested else None
        (module/'go.mod').write_text('module example.org/api\n\ngo 1.23\n')
        (module/'main.go').write_text('package main\nfunc main() {}\n')
        cmd=['go','-C','services/api','test','./...'] if nested else ['go','test','./...']
        base+=['[targets.app.jobs.test]','runner = "go"',
               'command = ['+', '.join('"'+s+'"' for s in cmd)+']',
               'inputs = ["'+('services/api/go.mod' if nested else 'go.mod')+'"]']
    else:
        base+=['[targets.app.jobs.test]','runner = "python"',
               'command = ["python3", "-m", "unittest"]']
    base+=['[targets.app.workflows]','ci = [{id="test", ref="job:test", depends_on=[]}]']
    (root/'.devtool.toml').write_text('\n'.join(base)+'\n')
    git(root,'add','.');git(root,'-c','user.name=Fixture','-c','user.email=fixture@example.org','commit','-qm','initial')
    return root


def plan(root):
    return provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)


def commit(root):
    git(root,'add','.');git(root,'-c','user.name=Fixture','-c','user.email=fixture@example.org','commit','-qm','updated')


def test_nested_go_command_uses_exact_tracked_module_and_pinned_setup(tmp_path):
    root=repo(tmp_path)
    row=plan(root)['nodes'][0]['provider']
    assert row['version_source']=='services/api/go.mod'
    assert row['workspace']['selected']['module']=='example.org/api'
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    assert graph['jobs'][0]['go_manifest']=='services/api/go.mod'
    assert 'go-version-file: services/api/go.mod' in workflow_yaml(graph)
    assert 'go-version-file: services/api/go.mod' in _workflow_yaml(plan(root))
    assert plan(root)==plan(root)  # deterministic, including reviewed source digest


def test_workspace_members_and_escape_refusal(tmp_path):
    root=repo(tmp_path)
    (root/'go.work').write_text('go 1.23\nuse (\n  ./services/api\n)\n')
    commit(root)
    original=plan(root)
    assert original['nodes'][0]['provider']['workspace']['workspace_members']==['services/api']
    for text in ('go 1.23\nuse ../outside\n',
                 'go 1.23\nuse ./services/api\nreplace example.org/remote => ../outside\n',
                 'go 1.23\nuse ./other\n'):
        (root/'go.work').write_text(text)
        with pytest.raises(ProviderError):plan(root)
    (root/'go.work').write_text('go 1.23\nuse ./services/api\n')
    with pytest.raises(ProviderError,match='frozen'):
        provider_plan(root,target='app',workflow='ci',devtool_sha=PIN,
                      expect_plan_sha=original['plan_sha256'])


def test_untracked_and_symlinked_nested_go_rejected(tmp_path):
    root=repo(tmp_path)
    (root/'services/api/go.mod').unlink()
    (root/'services/api/go.mod').symlink_to(root/'.devtool.toml')
    with pytest.raises(ProviderError,match='Symlink'):plan(root)
    (root/'services/api/go.mod').unlink()
    (root/'services/api/go.mod').write_text('module example.org/api\n\ngo 1.23\n')
    git(root,'rm','--cached','-q','services/api/go.mod')
    with pytest.raises(ProviderError):plan(root)


def test_go_race_requires_declared_cgo_and_linux_host(tmp_path):
    root=repo(tmp_path)
    cfg=root/'.devtool.toml'
    cfg.write_text(cfg.read_text().replace('"test", "./..."','"test", "-race", "./..."'))
    with pytest.raises(ProviderError,match='race'):plan(root)
    cfg.write_text(cfg.read_text().replace('inputs = ["services/api/go.mod"]',
                'inputs = ["services/api/go.mod"]\nenv = { CGO_ENABLED="1" }'))
    assert plan(root)['nodes'][0]['provider']['provider']=='go'
    with pytest.raises(ProviderError,match='race'):
        compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,host_profiles='macos-15-intel')
    assert compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)['jobs'][0]['race']


def add_python_project(root):
    (root/'pyproject.toml').write_text('[project]\nname="demo-pkg"\nversion="1.0"\n'
        '[project.scripts]\ndemo="demo:main"\n[build-system]\n'
        'requires=["setuptools==75.1.0"]\nbuild-backend="setuptools.build_meta"\n')
    (root/'requirements.txt').write_text('--require-hashes\n'
        'build==1.2.2 --hash=sha256:'+'a'*64+'\n'
        'setuptools==75.1.0 --hash=sha256:'+'b'*64+'\n')


def test_python_reviewed_backend_lock_and_version_pin(tmp_path):
    root=repo(tmp_path,python=True);add_python_project(root)
    conf=root/'.devtool.toml'
    conf.write_text(conf.read_text().replace('command = ["python3", "-m", "unittest"]',
        'command = ["python3", "-m", "unittest"]\nmetadata = {ci_python_version="3.11", ci_python_policy="reviewed"}'))
    commit(root)
    reviewed=plan(root)['nodes'][0]['provider']
    assert reviewed['version']=='3.11'
    assert reviewed['project']['backend']=='setuptools.build_meta'
    assert reviewed['project']['manager']=='pip'
    assert "python-version: '3.11'" in workflow_yaml(compile_graph(root,target='app',workflow='ci',devtool_sha=PIN))
    assert "python-version: '3.11'" in _workflow_yaml(plan(root))
    (root/'poetry.lock').write_text('untrusted second resolver')
    with pytest.raises(ProviderError,match='lock'):plan(root)
    (root/'poetry.lock').unlink()
    (root/'pyproject.toml').write_text((root/'pyproject.toml').read_text().replace('setuptools.build_meta','other.backend'))
    with pytest.raises(ProviderError,match='backend'):plan(root)


def test_unknown_python_version_and_missing_hash_policy_refused(tmp_path):
    root=repo(tmp_path,python=True);add_python_project(root)
    cfg=root/'.devtool.toml'
    cfg.write_text(cfg.read_text().replace('command = ["python3", "-m", "unittest"]',
                'command = ["python3", "-m", "unittest"]\nmetadata = {ci_python_version="3.17"}'))
    with pytest.raises(ProviderError,match='version'):plan(root)
    cfg.write_text(cfg.read_text().replace('3.17','3.13').replace('ci_python_version="3.13"',
                                                        'ci_python_version="3.13", ci_python_policy="reviewed"'))
    commit(root)
    (root/'requirements.txt').write_text('setuptools>=1\n')
    with pytest.raises(ProviderError,match='unpinned'):plan(root)


def wheel_bytes(path):
    di='demo_pkg-1.0.dist-info'
    files={di+'/METADATA':b'Metadata-Version: 2.1\nName: demo-pkg\nVersion: 1.0\n',
           di+'/WHEEL':b'Wheel-Version: 1.0\nTag: py3-none-any\n',
           di+'/entry_points.txt':b'[console_scripts]\ndemo=demo:main\n',
           'demo.py':b'def main():\n    print("ok")\n    return 0\n'}
    rows=[]
    for name,data in files.items():
        digest=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        rows.append([name,'sha256='+digest,str(len(data))])
    rows.append([di+'/RECORD','',''])
    buf=io.StringIO();writer=csv.writer(buf,lineterminator='\n');writer.writerows(rows)
    files[di+'/RECORD']=buf.getvalue().encode()
    path.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(path,'w') as z:
        for name,data in files.items():z.writestr(name,data)


def test_offline_isolated_wheel_install_and_entrypoint(tmp_path):
    root=repo(tmp_path,python=True);add_python_project(root);commit(root)
    wheel='dist/demo_pkg-1.0-py3-none-any.whl'
    wheel_bytes(root/wheel)
    checked=smoke_wheel(root,wheel,name='demo-pkg',version='1.0',entrypoint='demo')
    assert checked['installed_without_network'] and checked['invoked_exit_zero']
    assert not checked['release_qualified']
    with pytest.raises(ProjectReviewError,match='path'):
        smoke_wheel(root,'../demo.whl',name='demo-pkg',version='1.0',entrypoint='demo')
    with zipfile.ZipFile(root/wheel,'a') as z:z.writestr('demo.py',b'tampered')
    with pytest.raises(ValueError):
        smoke_wheel(root,wheel,name='demo-pkg',version='1.0',entrypoint='demo')


def test_nested_real_go_build_distribution_receipt_and_wrong_source(tmp_path):
    import json,os
    from devtool.actions_distributions import verify_distributions, DistributionError
    root=repo(tmp_path)
    module=root/'services/api';dest=module/'dist/demo'
    cfg=root/'.devtool.toml'
    cfg.write_text(cfg.read_text().replace('"test", "./..."','"build", "-o", "dist/demo", "."').replace(
        'inputs = ["services/api/go.mod"]',
        'inputs = ["services/api/go.mod"]\noutputs = ["services/api/dist/demo"]'))
    (root/'ci').mkdir()
    (root/'ci/devtool-distributions.json').write_text(json.dumps({
        'schema_version':1,'target':'app','workflow':'ci','distributions':[
         {'id':'binary','kind':'go','path':'services/api/dist/demo',
          'module':'example.org/api','goos':'linux','goarch':'amd64','cgo_enabled':False}]}))
    commit(root)
    result=plan(root)
    assert result['distributions'][0]['kind']=='go'
    # This is a reviewed Linux/amd64 *distribution* test, not a native-host
    # runnable test. Termux defaults to android/arm64 on many devices; relying
    # on ambient GOOS/GOARCH silently builds a different ELF target.
    run=subprocess.run(['go','-C','services/api','build','-o','dist/demo','.'],cwd=root,
                       env={**os.environ,'GOOS':'linux','GOARCH':'amd64',
                            'CGO_ENABLED':'0','GOTOOLCHAIN':'local'},
                       capture_output=True,text=True,timeout=90)
    assert run.returncode==0,run.stderr
    verified=verify_distributions(root,target='app',workflow='ci')
    assert verified['all_local_bytes_verified']
    assert verified['checks'][0]['details']['go_buildinfo']['module_matches_source']
    # Wrong declarations must still be refused: do not relax ELF validation
    # to conceal native Android/ARM64 host defaults.
    from devtool.actions_distributions import verify_go_binary
    with pytest.raises(DistributionError, match='architecture/format'):
        verify_go_binary(dest, goos='linux', goarch='arm64', cgo=False, module='example.org/api')
    with pytest.raises(DistributionError, match='GOOS/GOARCH/CGO'):
        verify_go_binary(dest, goos='linux', goarch='amd64', cgo=True, module='example.org/api')
    assert not verified['formal_release_qualified']
    (module/'go.mod').write_text((module/'go.mod').read_text()+'\n// modified after build\n')
    with pytest.raises(DistributionError,match='changed'):
        verify_distributions(root,target='app',workflow='ci')


def test_go_module_replace_and_workspace_version_mismatch_refuse(tmp_path):
    root=repo(tmp_path)
    (root/'services/api/go.mod').write_text((root/'services/api/go.mod').read_text()+
                                            'replace example.com/x => ../../outside\n')
    with pytest.raises(ProviderError,match='replace'):plan(root)
    (root/'services/api/go.mod').write_text('module example.org/api\n\ngo 1.23\n')
    (root/'go.work').write_text('go 1.24\nuse ./services/api\n');git(root,'add','go.work')
    with pytest.raises(ProviderError,match='version'):plan(root)


def test_reviewed_python_hosted_wheel_smoke_is_owned_and_hash_provisioned(tmp_path):
    import json
    root=repo(tmp_path,python=True);add_python_project(root)
    cfg=root/'.devtool.toml'
    cfg.write_text(cfg.read_text().replace('"unittest"',
        '"build", "--wheel", "--no-isolation", "--outdir", "dist"').replace(
        'command = ["python3", "-m", "build", "--wheel", "--no-isolation", "--outdir", "dist"]',
        'command = ["python3", "-m", "build", "--wheel", "--no-isolation", "--outdir", "dist"]\n'
        'inputs = ["pyproject.toml", "requirements.txt"]\n'
        'outputs = ["dist/demo_pkg-1.0-py3-none-any.whl"]\n'
        'metadata = {ci_python_policy="reviewed", ci_wheel_smoke=true}'))
    (root/'ci').mkdir()
    (root/'ci/devtool-distributions.json').write_text(json.dumps({
        'schema_version':1,'target':'app','workflow':'ci','distributions':[
         {'id':'wheel','kind':'wheel','path':'dist/demo_pkg-1.0-py3-none-any.whl',
          'name':'demo-pkg','version':'1.0','entrypoints':['demo']}
        ]}))
    commit(root)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    assert graph['jobs'][0]['wheel_smoke'] and graph['jobs'][0]['python_package']
    yaml=workflow_yaml(graph)
    assert 'Install tracked hash-locked Python build requirements' in yaml
    assert 'pip install --disable-pip-version-check --require-hashes -r requirements.txt' in yaml
    cfg.write_text(cfg.read_text().replace('ci_wheel_smoke=true','ci_wheel_smoke=false'))
    assert not compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)['jobs'][0]['wheel_smoke']


def test_python_coverage_merges_disjoint_files_and_refuses_duplicate_or_entity(tmp_path):
    from devtool.actions_rm08_projects import merge_coverage_xml
    root=tmp_path/'work';(root/'dist').mkdir(parents=True)
    for name,source,hit in [('a.xml','a.py',1),('b.xml','b.py',0)]:
        (root/'dist'/name).write_text(f'<coverage><packages><package><classes><class filename="{source}"><lines><line number="1" hits="{hit}" /></lines></class></classes></package></packages></coverage>')
    result=merge_coverage_xml(root,['dist/a.xml','dist/b.xml'])
    assert (result['covered_lines'],result['valid_lines'],result['line_rate'])==(1,2,.5)
    assert not result['tests_authenticated']
    (root/'dist/b.xml').write_text((root/'dist/b.xml').read_text().replace('b.py','a.py'))
    with pytest.raises(ProjectReviewError,match='duplicate'):
        merge_coverage_xml(root,['dist/a.xml','dist/b.xml'])
    (root/'dist/b.xml').write_text('<!DOCTYPE coverage [ <!ENTITY x "boom"> ]><coverage/>')
    with pytest.raises(ProjectReviewError,match='entity'):
        merge_coverage_xml(root,['dist/b.xml'])
