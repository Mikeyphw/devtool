"""R1R29-H1: Android Python OS labels, actual Termux authority and safe failures.

No test reaches Android shared storage or GitHub and no Android binary is
claimed native-qualified by Linux CI tests.
"""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

REPO=Path(__file__).resolve().parents[1]

def load(name):
    path=REPO/'tools'/name
    spec=importlib.util.spec_from_file_location('fc08_r1r29_h1_'+name.replace('.','_'),path)
    assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

native=load('fc08_r1r28_termux.py')
storage=load('fc08_r1r29_storage_probe.py')
wrapper=load('fc08_r1r29_native_accept.py')


def termux(monkeypatch,mod,label='Android',arch='aarch64',prefix='/data/data/com.termux/files/usr',kernel='Linux'):
    monkeypatch.setattr(mod.platform,'system',lambda:label)
    monkeypatch.setattr(mod.platform,'machine',lambda:arch)
    monkeypatch.setattr(mod.sys,'prefix',prefix)
    monkeypatch.setattr(mod.os,'uname',lambda:SimpleNamespace(sysname=kernel))
    # This fake exists() applies only to the guarded Termux install prefix.
    orig=Path.exists
    monkeypatch.setattr(Path,'exists',lambda p: True if str(p)=='/data/data/com.termux/files/usr' else orig(p))
    monkeypatch.setattr(mod.shutil,'which',lambda cmd: '/usr/bin/'+cmd)


@pytest.mark.parametrize('label',['Android','Linux'])
def test_real_android_linux_kernel_and_termux_prefix_accepted(label,monkeypatch):
    termux(monkeypatch,native,label=label)
    native.environment()
    storage._native()


@pytest.mark.parametrize('label,arch,prefix,kernel',[
    ('Windows','aarch64','/data/data/com.termux/files/usr','Linux'),
    ('Android','x86_64','/data/data/com.termux/files/usr','Linux'),
    ('Android','aarch64','/usr/local','Linux'),
    ('Android','aarch64','/data/data/com.termux/files/usr','Darwin'),
])
def test_false_android_hosts_refused(monkeypatch,label,arch,prefix,kernel):
    termux(monkeypatch,native,label=label,arch=arch,prefix=prefix,kernel=kernel)
    with pytest.raises(RuntimeError,match='NATIVE_TERMUX_ARM64_REQUIRED'):
        native.environment()
    with pytest.raises(ValueError,match='ACTUAL_NATIVE_TERMUX_REQUIRED'):
        storage._native()


def test_missing_meson_reports_an_exact_safe_code(monkeypatch):
    termux(monkeypatch,native)
    monkeypatch.setattr(native.shutil,'which',lambda name: None if name=='meson' else '/usr/bin/'+name)
    with pytest.raises(RuntimeError,match='NATIVE_TOOL_UNAVAILABLE_MESON'):
        native.environment()
    assert wrapper.safe_category(RuntimeError('NATIVE_TOOL_UNAVAILABLE_MESON'))=='NATIVE_TOOL_UNAVAILABLE_MESON'


def test_unknown_error_never_prints_raw_text_or_secret():
    for x in [RuntimeError('NATIVE_TOKEN=secret'),ValueError('/sdcard/Download/my-private-file.zip'),
              RuntimeError('NATIVE_TOOL_UNAVAILABLE_TOKEN')]:
        assert wrapper.safe_category(x)=='NATIVE_ACCEPTANCE_UNCLASSIFIED'
    assert 'secret' not in json.dumps(wrapper.blocked_receipt(phase='preflight',category='NATIVE_ACCEPTANCE_UNCLASSIFIED'))


def test_native_failure_receipt_created_before_any_canary(monkeypatch,tmp_path,capsys):
    out=tmp_path/'native.json'
    class Native:
        def environment(self): raise RuntimeError('NATIVE_TOOL_UNAVAILABLE_MESON')
        def native_accept(self,*args):raise AssertionError('BUILD_MUST_NOT_START')
    class Storage:
        def probe(self,*args,**kw):raise AssertionError('STORAGE_MUST_NOT_START')
    monkeypatch.setattr(wrapper,'_load',lambda name: Native() if 'termux' in name else Storage())
    monkeypatch.setattr(sys,'argv',['runner','--repo',str(tmp_path),'--out',str(out)])
    assert wrapper.main()==2
    doc=json.loads(out.read_text())
    assert doc['phase']=='native-environment'
    assert doc['error_category']=='NATIVE_TOOL_UNAVAILABLE_MESON'
    assert doc['native_termux_arm64_acceptance_passed'] is False
    assert doc['shared_storage_owned_canary_passed'] is False
    assert doc['release_qualified'] is False
    assert 'NATIVE_TOOL_UNAVAILABLE_MESON' in capsys.readouterr().err


def test_storage_readonly_failure_receipt_created(monkeypatch,tmp_path,capsys):
    out=tmp_path/'storage.json';file=tmp_path/'artifact.zip';file.write_bytes(b'fake')
    monkeypatch.setattr(storage,'probe',lambda *a,**k: (_ for _ in ()).throw(ValueError('ARTIFACT_SYMLINK_REFUSED')))
    monkeypatch.setattr(sys,'argv',['probe','--artifact',str(file),'--out',str(out)])
    assert storage.main()==2
    doc=json.loads(out.read_text())
    assert doc['phase']=='read-only-artifact-and-optional-canary'
    assert doc['error_category']=='ARTIFACT_SYMLINK_REFUSED'
    assert doc['artifact_deleted'] is False
    assert not doc['release_qualified']


def test_storage_failure_does_not_echo_unknown_private_path(monkeypatch,tmp_path,capsys):
    out=tmp_path/'storage.json'
    monkeypatch.setattr(storage,'probe',lambda *a,**k: (_ for _ in ()).throw(ValueError('/my/private/secret.zip')))
    monkeypatch.setattr(sys,'argv',['probe','--artifact','/my/private/secret.zip','--out',str(out)])
    assert storage.main()==2
    assert 'secret' not in out.read_text()
    assert 'secret' not in capsys.readouterr().err
    assert json.loads(out.read_text())['error_category']=='STORAGE_PROBE_UNCLASSIFIED'


def test_native_success_stages_with_no_canary(monkeypatch,tmp_path):
    called=[];repo=tmp_path/'repo';repo.mkdir()
    class Native:
        def environment(self):called.append('environment')
        def native_accept(self,root):
            called.append('native');return {'source':{'sha':'a'*40,'tree':'b'*40}}
    class Storage:
        def probe(self,*args,**kwargs):called.append('storage');return {}
    monkeypatch.setattr(wrapper,'_load',lambda n:Native() if 'termux' in n else Storage())
    stages=[]
    proof=wrapper.accept(repo,on_stage=stages.append)
    assert called==['native']
    assert stages==['reviewed-native-import','native-build','shared-storage-observation']
    assert proof['native_termux_arm64_acceptance_passed'] is True
    assert proof['release_qualified'] is False
