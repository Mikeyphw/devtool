"""NEXT-04 adversarial distribution evidence and bounded provider export tests."""
from __future__ import annotations
import base64,csv,hashlib,io,json,os,subprocess,tarfile,zipfile
from pathlib import Path
import pytest
from devtool.actions_distributions import (DistributionError, distribution_contract,
    verify_distributions,verify_go_binary,verify_wheel,verify_sdist)
from devtool.actions_language_providers import ProviderError,provider_plan,provider_export,_workflow_yaml

PIN='d'*40

def git(root,*args):
    p=subprocess.run(['git','-C',str(root),*args],text=True,capture_output=True,check=True)
    return p.stdout.strip()

def repo(tmp, *, kind='go'):
    root=tmp/'consumer';root.mkdir()
    git(root,'init','-q')
    (root/'ci').mkdir()
    lines=['[project]','runner = "python"','[workspace]','default_target = "app"',
           '[wrapper]','schema = 2','[targets.app]','runner = "python"','root = "."']
    if kind=='go':
        lines+=['[targets.app.jobs.compile]','runner = "go"',
                'command = ["go", "build", "-o", "dist/hello", "."]',
                'inputs = ["go.mod"]','outputs = ["dist/hello"]']
        (root/'go.mod').write_text('module example.org/hello\n\ngo 1.23\n')
        (root/'main.go').write_text('package main\nfunc main() {}\n')
        contract={'schema_version':1,'target':'app','workflow':'ci','distributions':[
            {'id':'hello','kind':'go','path':'dist/hello','module':'example.org/hello',
             'goos':'linux','goarch':'amd64','cgo_enabled':False}]}
    else:
        lines+=['[targets.app.jobs.compile]','runner = "python"',
                'command = ["python3", "-m", "build", "--wheel", "--no-isolation", "--outdir", "dist"]',
                'inputs = ["pyproject.toml", "requirements.txt"]',
                'outputs = ["dist/demo_pkg-1.0-py3-none-any.whl"]']
        (root/'pyproject.toml').write_text('[project]\nname="demo-pkg"\nversion="1.0"\n[project.scripts]\ndemo="demo:main"\n[build-system]\nrequires=["setuptools"]\nbuild-backend="setuptools.build_meta"\n')
        (root/'requirements.txt').write_text('--require-hashes\nbuild==1.2.2 --hash=sha256:' + 'a'*64 + '\n')
        contract={'schema_version':1,'target':'app','workflow':'ci','distributions':[
            {'id':'wheel','kind':'wheel','path':'dist/demo_pkg-1.0-py3-none-any.whl',
             'name':'demo-pkg','version':'1.0','entrypoints':['demo']}]}
    lines+=['[targets.app.workflows]','ci = [{id="compile", ref="job:compile", depends_on=[]}]']
    (root/'.devtool.toml').write_text('\n'.join(lines)+'\n')
    (root/'ci/devtool-distributions.json').write_text(json.dumps(contract,indent=2)+'\n')
    git(root,'add','.');git(root,'-c','user.name=T','-c','user.email=t@example.org','commit','-qm','source')
    return root

def build_go(root):
    (root/'dist').mkdir(exist_ok=True)
    subprocess.run(['go','build','-o','dist/hello','.'],cwd=root,env={**os.environ,'CGO_ENABLED':'0','GOTOOLCHAIN':'local'},check=True,timeout=90,capture_output=True)

def wheel_file(path, *, metadata_version='1.0', malicious=False, bad_record=False):
    di='demo_pkg-1.0.dist-info'; contents={
        di+'/METADATA':f'Metadata-Version: 2.1\nName: demo-pkg\nVersion: {metadata_version}\n'.encode(),
        di+'/WHEEL':b'Wheel-Version: 1.0\nTag: py3-none-any\n',
        di+'/entry_points.txt':b'[console_scripts]\ndemo = demo:main\n',
        'demo.py':b'def main(): return 0\n'}
    if malicious:contents['../escape']=b'hi'
    rec=io.StringIO();w=csv.writer(rec,lineterminator='\n')
    for name,content in contents.items():
        checksum=base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=').decode()
        w.writerow([name,'sha256='+checksum,len(content)])
    w.writerow([di+'/RECORD','','']);contents[di+'/RECORD']=rec.getvalue().encode()
    if bad_record: contents['demo.py']=b'tampered'
    path.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(path,'w') as z:
        for name,content in contents.items(): z.writestr(name,content)

def test_real_go_build_and_verification_with_source_pin(tmp_path):
    root=repo(tmp_path)
    reviewed=provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)
    assert reviewed['distributions'][0]['kind']=='go'
    assert reviewed['reviewed_source_digests']['ci/devtool-distributions.json']
    assert not (root/'dist').exists()
    build_go(root)
    result=verify_distributions(root,target='app',workflow='ci')
    assert result['all_local_bytes_verified'] and len(result['checks'][0]['sha256'])==64
    assert result['checks'][0]['details']['binary_header_verified']
    assert not result['formal_release_qualified'] and not result['hosted_run_attested']
    yaml=_workflow_yaml(reviewed)
    assert 'actions provider-distributions' in yaml
    assert yaml.index('Refuse pre-existing candidate outputs')<yaml.index('Run native DAG')<yaml.index('Verify declared distributions')<yaml.index('Record source-bound evidence')


def test_missing_output_is_not_pass(tmp_path):
    root=repo(tmp_path)
    assert distribution_contract(root,target='app',workflow='ci')['read_only']
    with pytest.raises(DistributionError,match='missing'):
        verify_distributions(root,target='app',workflow='ci')
    assert not (root/'dist').exists()
    assert git(root,'status','--porcelain')==''


def test_wrong_go_architecture_and_cgo_rejected(tmp_path):
    root=repo(tmp_path);build_go(root)
    binary=root/'dist/hello'
    with pytest.raises(DistributionError,match='architecture'):
        verify_go_binary(binary,goos='linux',goarch='arm64',cgo=False)
    with pytest.raises(DistributionError,match='CGO'):
        verify_go_binary(binary,goos='windows',goarch='amd64',cgo=True)
    bad=root/'dist/fake';bad.write_bytes(b'not an ELF')
    with pytest.raises(DistributionError,match='architecture'):
        verify_go_binary(bad,goos='linux',goarch='amd64',cgo=False)


def test_wheel_record_metadata_and_scripts(tmp_path):
    root=repo(tmp_path,kind='python');p=root/'dist/demo_pkg-1.0-py3-none-any.whl'
    plan=provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)
    assert plan['distributions'][0]['kind']=='wheel'
    assert 'pip install --require-hashes -r requirements.txt' in _workflow_yaml(plan)
    wheel_file(p)
    verified=verify_distributions(root,target='app',workflow='ci')
    assert verified['checks'][0]['details']['record_sha256_verified']
    assert verified['checks'][0]['details']['entrypoints_verified']==['demo']
    assert not verified['signatures_authenticated']
    wheel_file(p,metadata_version='9.0')
    with pytest.raises(DistributionError,match='mismatch'):
        verify_distributions(root,target='app',workflow='ci')
    wheel_file(p,bad_record=True)
    with pytest.raises(DistributionError,match='hash'):
        verify_distributions(root,target='app',workflow='ci')
    wheel_file(p,malicious=True)
    with pytest.raises(DistributionError,match='Unsafe'):
        verify_distributions(root,target='app',workflow='ci')


def test_requirements_policy_must_be_hash_pinned(tmp_path):
    root=repo(tmp_path,kind='python')
    (root/'requirements.txt').write_text('requests>=2.0\n')
    with pytest.raises(DistributionError,match='unpinned'):
        distribution_contract(root,target='app',workflow='ci')
    with pytest.raises(ProviderError):
        provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)


def test_unowned_path_contract_rejected(tmp_path):
    root=repo(tmp_path)
    cfg=root/'ci/devtool-distributions.json';j=json.loads(cfg.read_text());j['distributions'][0]['path']='dist/not-owned';cfg.write_text(json.dumps(j))
    with pytest.raises(ProviderError,match='exactly one'):
        provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)


def test_cross_target_go_job_environment_and_unsupported_cgo(tmp_path):
    root=repo(tmp_path)
    config=root/'.devtool.toml';data=config.read_text()
    config.write_text(data.replace('outputs = ["dist/hello"]','outputs = ["dist/hello"]\nenv = { GOOS="linux", GOARCH="amd64", CGO_ENABLED="0" }'))
    assert provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)['providers']==['go']
    config.write_text(config.read_text().replace('CGO_ENABLED="0"','CGO_ENABLED="1"').replace('GOARCH="amd64"','GOARCH="arm64"'))
    with pytest.raises(ProviderError,match='CGO'):
        provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)


def test_manifest_schema_path_and_source_drift(tmp_path):
    root=repo(tmp_path);a=provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)
    conf=root/'ci/devtool-distributions.json';j=json.loads(conf.read_text())
    j['distributions'][0]['path']='../secret';conf.write_text(json.dumps(j))
    with pytest.raises(DistributionError,match='relative'):
        distribution_contract(root,target='app',workflow='ci')
    j['distributions'][0]['path']='dist/hello';conf.write_text(json.dumps(j))
    with pytest.raises(ProviderError,match='frozen'):
        provider_plan(root,target='app',workflow='ci',devtool_sha=PIN,expect_plan_sha=a['plan_sha256'])


def test_sdist_metadata_archive_safety(tmp_path):
    dest=tmp_path/'demo-pkg-1.0.tar.gz'
    with tarfile.open(dest,'w:gz') as f:
        raw=b'Metadata-Version: 2.1\nName: demo-pkg\nVersion: 1.0\n'
        info=tarfile.TarInfo('demo-pkg-1.0/PKG-INFO');info.size=len(raw);f.addfile(info,io.BytesIO(raw))
    assert verify_sdist(dest,name='demo-pkg',version='1.0')['metadata_verified']
    with pytest.raises(DistributionError,match='mismatch'):
        verify_sdist(dest,name='demo-pkg',version='2.0')
    with tarfile.open(dest,'w:gz') as f:
        info=tarfile.TarInfo('demo-pkg-1.0/link');info.type=tarfile.SYMTYPE;info.linkname='../../oops';f.addfile(info)
    with pytest.raises(DistributionError,match='symlink'):
        verify_sdist(dest,name='demo-pkg',version='1.0')


def test_hostile_output_path_cannot_become_workflow_yaml(tmp_path):
    root=repo(tmp_path)
    contract=root/'ci/devtool-distributions.json';data=json.loads(contract.read_text())
    data['distributions'][0]['path']='dist/file\npermissions: write-all'
    contract.write_text(json.dumps(data))
    with pytest.raises(DistributionError,match='bounded'):
        distribution_contract(root,target='app',workflow='ci')
    with pytest.raises(ProviderError):
        provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)


def test_missing_hash_pinned_build_tool_fails_closed(tmp_path):
    root=repo(tmp_path,kind='python')
    (root/'requirements.txt').write_text('--require-hashes\n')
    with pytest.raises(DistributionError,match='hash-pinned build tool'):
        distribution_contract(root,target='app',workflow='ci')


def test_unreviewed_python_build_output_directory_blocked(tmp_path):
    root=repo(tmp_path,kind='python');config=root/'.devtool.toml'
    config.write_text(config.read_text().replace('"--outdir", "dist"','"--outdir", "outside"'))
    with pytest.raises(ProviderError,match='outdir dist'):
        provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)


def test_go_header_spoof_without_matching_buildinfo_blocked(tmp_path):
    root=repo(tmp_path);target=root/'dist/forged'
    target.parent.mkdir()
    header=bytearray(512);header[:4]=b'\x7fELF';header[4]=2;header[5]=1
    header[18:20]=(62).to_bytes(2,'little');target.write_bytes(header)
    with pytest.raises(DistributionError,match='module metadata'):
        verify_go_binary(target,goos='linux',goarch='amd64',cgo=False,module='example.org/hello')
