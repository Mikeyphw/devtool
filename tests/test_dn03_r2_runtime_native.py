"""DN03-R2: source/run acceptance and SHA512-only legacy runtime negatives."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest
import yaml

from devtool import dn03_runtime_only as net6
from devtool import actions_dn03_dotnet as dn03
import importlib.util

SOURCE = Path(__file__).parents[1] / 'tools' / 'dn03_r06d_acceptance.py'
spec = importlib.util.spec_from_file_location('dn03_r06d', SOURCE)
assert spec and spec.loader
r06d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r06d)


def _archive(path: Path, *, attack: str = '', include_lib: bool = True) -> str:
    prefix='shared/Microsoft.NETCore.App/6.0.36/'
    with tarfile.open(path,'w:gz') as tf:
        for name in range(32):
            data=b'x' * 20
            item=tarfile.TarInfo('./'+prefix+('libcoreclr.so' if name==0 and include_lib else f'file_{name}.dll'))
            item.size=len(data)
            tf.addfile(item,io.BytesIO(data))
        if attack:
            extra=tarfile.TarInfo('./'+prefix+attack)
            extra.size=5
            tf.addfile(extra,io.BytesIO(b'poison'))
    return hashlib.sha512(path.read_bytes()).hexdigest()


def test_dn03_r2_microsoft_runtime_only_pin_is_exact():
    assert len(net6.SHA512)==128
    assert net6.SHA512.startswith('afb6018fcabec468')
    assert net6.URL.startswith('https://download.visualstudio.microsoft.com/download/pr/')
    assert net6.ARCHIVE=='dotnet-runtime-6.0.36-linux-x64.tar.gz'
    assert dn03.RUNTIME_ARCHIVE_SHA512==net6.SHA512
    assert net6._safe_member('./')==()
    with pytest.raises(net6.RuntimeOnlyError):
        net6._safe_member('../etc/passwd')


def test_dn03_r2_runtime_only_install_atomic_and_create_only(tmp_path,monkeypatch):
    archive=tmp_path/'runtime.tar.gz'
    checksum=_archive(archive)
    monkeypatch.setattr(net6,'SHA512',checksum)
    root=tmp_path/'dotnet';root.mkdir()
    receipt=tmp_path/'receipt.json'
    result=net6.install_archive(archive,root,receipt=receipt)
    assert result['files']==32
    assert result['archive_sha512']==checksum
    assert result['sdk_installed_by_this_provisioner'] is False
    assert (root/'shared/Microsoft.NETCore.App/6.0.36/libcoreclr.so').is_file()
    assert json.loads(receipt.read_text())['runtime']=='6.0.36'
    with pytest.raises(net6.RuntimeOnlyError,match='Existing runtime'):
        net6.install_archive(archive,root,receipt=receipt)
    assert list((root/'shared/Microsoft.NETCore.App').glob('.dn03-runtime-stage-*'))==[]


def test_dn03_r2_runtime_only_refuses_wrong_hash_and_archive_traversal(tmp_path,monkeypatch):
    archive=tmp_path/'runtime.tar.gz'
    digest=_archive(archive, attack='../../../../evil')
    with pytest.raises(net6.RuntimeOnlyError,match='SHA-512 differs'):
        net6.verify_archive(archive)
    monkeypatch.setattr(net6,'SHA512',digest)
    root=tmp_path/'dotnet';root.mkdir()
    with pytest.raises(net6.RuntimeOnlyError,match='traversal'):
        net6.install_archive(archive,root)
    assert not (root/'shared/Microsoft.NETCore.App/6.0.36').exists()
    assert not (tmp_path/'evil').exists()


def test_dn03_r2_runtime_only_refuses_missing_lib_and_symlink_root(tmp_path,monkeypatch):
    archive=tmp_path/'runtime.tar.gz'
    digest=_archive(archive, include_lib=False)
    monkeypatch.setattr(net6,'SHA512',digest)
    root=tmp_path/'dotnet';root.mkdir()
    with pytest.raises(net6.RuntimeOnlyError,match='Incomplete'):
        net6.install_archive(archive,root)
    symlink=tmp_path/'linked'
    symlink.symlink_to(root,target_is_directory=True)
    with pytest.raises(net6.RuntimeOnlyError,match='regular directory'):
        net6.install_archive(archive,symlink)


def test_dn03_r2_github_identity_fail_closed():
    env={'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'Mikeyphw/devtool',
         'RUNNER_OS':'Linux','RUNNER_ARCH':'X64',
         'EXPECTED_DEVTOOL_SHA':'a'*40,'GITHUB_SHA':'b'*40,
         'GITHUB_RUN_ID':'42','GITHUB_RUN_ATTEMPT':'1'}
    assert r06d._context(env)['devtool_head']=='a'*40
    for key,bad in [('GITHUB_ACTIONS','false'),('GITHUB_REPOSITORY','other/repo'),
                    ('RUNNER_OS','Windows'),('RUNNER_ARCH','ARM64'),
                    ('EXPECTED_DEVTOOL_SHA','main'),('GITHUB_RUN_ID','0'),
                    ('GITHUB_RUN_ATTEMPT','oops')]:
        with pytest.raises(r06d.R06DError):
            r06d._context({**env,key:bad})


def test_dn03_r2_real_consumer_receipt_must_include_five_nodes(tmp_path,monkeypatch):
    evidence=tmp_path/'evidence';native=evidence/'native';native.mkdir(parents=True)
    consumer=tmp_path/'consumer';(consumer/'.ci-results/r06b').mkdir(parents=True)
    for f in r06d.TRX:
        (consumer/'.ci-results/r06b'/f).write_bytes(b'test fixture')
    for file in ('plan.json','preflight.json','invoke.json'):
        (native/file).write_text('{}')
    expected={'devtool_sha':'a'*40,'consumer_sha':r06d.CONSUMER_SHA,
              'github':{'run_id':42},'host':{},'release_qualified':False}
    monkeypatch.setattr(r06d,'inspect',lambda *args,**kwargs:expected)
    (evidence/'preflight.json').write_text(json.dumps(expected))
    receipt={'status':'HOSTED_NATIVE_LOCKED_DOTNET_PASS','consumer_sha':r06d.CONSUMER_SHA,
             'devtool_sha':'a'*40,'node_count':5,'native_nodes':list(r06d.NODES),
             'release_qualified':False,
             'test_receipts':{t:{'passed':1,'executed':1,'failed':0,'notExecuted':0} for t in r06d.TRX},
             'plan_sha256':r06d._sha(native/'plan.json'),
             'preflight_sha256':r06d._sha(native/'preflight.json'),
             'invoke_sha256':r06d._sha(native/'invoke.json')}
    (native/'receipt.json').write_text(json.dumps(receipt))
    result=r06d._verify(evidence,tmp_path,consumer,tmp_path,tmp_path, {})
    assert result['native_command_dag_qualified'] is True
    assert result['rm09_generated_node_dag_qualified'] is False
    assert not result['release_qualified']
    receipt['native_nodes']=list(reversed(r06d.NODES))
    (native/'receipt.json').write_text(json.dumps(receipt))
    with pytest.raises(r06d.R06DError,match='5-node'):
        r06d._verify(evidence,tmp_path,consumer,tmp_path,tmp_path,{})


def test_dn03_r2_hosted_workflow_pinned_and_shell_valid():
    path=Path(__file__).parents[1]/'.github/workflows/devtool-dn03-r06d-hosted-native.yml'
    text=path.read_text()
    x=yaml.safe_load(text)
    steps=x['jobs']['actual-native-dotnet']['steps']
    assert len(steps) >= 14
    assert '549d6cd0d08894dfac4950e362e437eb06010729' in text
    assert '26b0ec14cb23fa6904739307f278c14f94c95bf1' in text
    assert 'cf430e030ddbb5b0abf93d22962f4752f3646cd9' in text
    assert 'dotnet-version: \'6.0.428\'' not in text
    assert '--locked-mode' not in text  # Consumer owns native locked commands.
    assert 'invoke --target svmods ci-hosted-dotnet' in text
    assert 'tools/dn03_r06d_acceptance.py verify' in text
    for step in steps:
        if 'run' in step:
            proc=subprocess.run(['bash','-n'],input=step['run'],capture_output=True,text=True)
            assert proc.returncode==0, (step['name'],proc.stderr)


def test_dn03_r2_generated_native_job_runtime_only_after_source(monkeypatch, tmp_path):
    testfile=Path(__file__).parent/'test_dn03_dotnet_export.py'
    suite_spec=importlib.util.spec_from_file_location('dn03_r1_fixture',testfile)
    assert suite_spec and suite_spec.loader
    suite=importlib.util.module_from_spec(suite_spec)
    suite_spec.loader.exec_module(suite)
    suite.fixture(monkeypatch,legacy=True)
    source=suite.repo(tmp_path)
    text, review=dn03.compile_dn03(source,target='app',workflow='ci',devtool_sha=suite.PIN)
    assert review['runtime_only_legacy'] is True
    assert review['runtime_archive_sha512']==net6.SHA512
    parsed=yaml.safe_load(text)
    for job in parsed['jobs'].values():
        steps=job['steps']
        names=[i['name'] for i in steps]
        assert names.index('DN03 bind isolated exact .NET SDK installation') < names.index('Pinned .NET SDK')
        assert names.index('Acquire immutable Devtool runtime') < names.index('DN03 install SHA-512-pinned .NET 6 runtime only')
        assert names.index('DN03 install SHA-512-pinned .NET 6 runtime only') < names.index('DN03 verify .NET host and record local source-bound evidence')
        assert names.index('DN03 verify .NET host and record local source-bound evidence') < names.index('Verify dependencies and run native target-local node')
        assert 'Legacy .NET 6 runtime compatibility (EOL)' not in names
        host=next(i['run'] for i in steps if i['name'].startswith('DN03 verify'))
        assert 'runtime_receipt_sha256' in host
        for step in steps:
            if 'run' in step:
                p=subprocess.run(['bash','-n'],input=step['run'],capture_output=True,text=True)
                assert p.returncode==0,(step['name'],p.stderr)


def test_dn03_r06d_acceptance_uses_installed_devtool_interpreter():
    """Regression for hosted 41724269345 / 41724243983 import failure."""
    path = Path(__file__).parents[1] / '.github/workflows/devtool-dn03-r06d-hosted-native.yml'
    workflow = yaml.safe_load(path.read_text(encoding='utf-8'))
    steps = workflow['jobs']['actual-native-dotnet']['steps']
    names = {step['name']: step for step in steps}
    interpreter = '"$RUNNER_TEMP/dn03-venv/bin/python"'
    bootstrap = names['Install exact Git checkout as Devtool Python runtime']['run']
    assert interpreter in bootstrap
    assert 'from devtool.actions_rm09_dotnet import verify_dotnet_host' in bootstrap
    for name, mode in (
        ('Verify source, exact SDK, and runtime receipt before native execution', 'preflight'),
        ('Bind actual native receipt to immutable host and source identities', 'verify'),
    ):
        command = names[name]['run']
        assert f'{interpreter} tools/dn03_r06d_acceptance.py {mode}' in command
        assert f'python3 tools/dn03_r06d_acceptance.py {mode}' not in command
    assert names['Install exact Git checkout as Devtool Python runtime']['run'].find('pip install') < bootstrap.find('DN03_R06D_VENV_PROVIDER_IMPORT_PASS')
