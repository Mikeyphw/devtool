"""ACTIONS-05: module/ref safety, source matching, package signing, no false promotion."""
from __future__ import annotations
import io, os, subprocess, zipfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from devtool.github_actions import GitHubActionsError, actions_cli
from devtool.actions_android import android_discover, android_plan, inspect_android_zip, android_collect, _project_root
from devtool.cli import build_parser

SHA='a'*40
CERT='b'*64


def project(tmp_path):
    p=tmp_path/'android';(p/'app'/'src/main').mkdir(parents=True)
    (p/'settings.gradle.kts').write_text('include(":app")\n')
    (p/'app/build.gradle.kts').write_text('plugins { id("com.android.application") }\nandroid { defaultConfig { applicationId = "org.example.app" } }\n')
    (p/'app/src/main/AndroidManifest.xml').write_text('<manifest package="org.example.app"/>')
    (p/'gradlew').write_text('gradle wrapper placeholder')
    (p/'gradle/wrapper').mkdir(parents=True)
    (p/'gradle/wrapper/gradle-wrapper.properties').write_text('distributionUrl=x')
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    for cmd in (["config","user.name","Test"],["config","user.email","test@invalid.test"],["add","-A"],["commit","-qm","baseline"]):
        subprocess.run(['git','-C',str(tmp_path),*cmd],check=True)
    return p


def test_parser_and_registry_nested_android_commands():
    parser=build_parser()
    for cmd in ('discover','plan','run','collect','verify'):
        args=['actions','android',cmd]
        if cmd in ('plan','run'):args+=['--ref','main']
        if cmd in ('collect','verify'):args+=['--run-id','25','--artifact-id','72']
        parsed=parser.parse_args(args)
        assert parsed.actions_command=='android' and parsed.android_command==cmd
        assert parsed.github_repo==''


def test_local_project_discovery_and_no_dangerous_paths(tmp_path):
    project(tmp_path)
    out=android_discover(tmp_path)
    assert out['project_dir']=='android' and out['modules'][0]['module']=='app'
    assert out['modules'][0]['application_id']=='org.example.app'
    for bad in ('../other','/tmp','android/../other','android\\other'):
        with pytest.raises(GitHubActionsError):_project_root(tmp_path,bad)


def test_build_plan_pins_local_head_and_rejects_mismatch(tmp_path,monkeypatch):
    project(tmp_path)
    local=subprocess.check_output(['git','-C',str(tmp_path),'rev-parse','HEAD'],text=True).strip()
    calls=[]
    def dispatch(client,slug,workflow,ref,inputs,*,execute):
        calls.append((execute,inputs))
        return {'pre_dispatch_sha':local,'repository':slug,'state':'validated-plan','dispatched':execute,'resume_existing':False}
    monkeypatch.setattr('devtool.actions_android.dispatch_workflow',dispatch)
    out=android_plan(object(),'Owner/repo',tmp_path,module='app',variant='debug',fmt='apk',ref='main')
    assert not out['dispatched'] and out['project_dir']=='android'
    assert calls==[(False,{'module':'app','variant':'debug','format':'apk','project_dir':'android'})]
    out=android_plan(object(),'Owner/repo',tmp_path,module='app',variant='debug',fmt='apk',ref='main',execute=True)
    assert out['dispatched'] and len(calls)==3
    monkeypatch.setattr('devtool.actions_android.dispatch_workflow',lambda *a,**k:{'pre_dispatch_sha':'f'*40})
    with pytest.raises(GitHubActionsError,match='differs'):
        android_plan(object(),'Owner/repo',tmp_path,module='app',variant='debug',fmt='apk',ref='main',execute=True)
    (tmp_path/'local-uncommitted').write_text('changes')
    with pytest.raises(GitHubActionsError,match='clean'):
        android_plan(object(),'Owner/repo',tmp_path,module='app',variant='debug',fmt='apk',ref='main',execute=True)


def zipped(tmp_path, files):
    p=tmp_path/'output.zip'
    with zipfile.ZipFile(p,'w') as z:
        for name in files:z.writestr(name,b'contents')
    return p


def test_android_package_inspection_requires_tool_verified_identity(tmp_path,monkeypatch):
    package=zipped(tmp_path,['app/build/outputs/apk/debug/app-debug.apk'])
    monkeypatch.setattr('devtool.actions_android._inspect_apk',lambda p:{'package':'org.example.app',
        'version_code':'4','version_name':'1.2','signer_sha256':CERT,'signature_verified':True,'manifest_verified':True})
    result=inspect_android_zip(package,fmt='apk',variant='debug',expected_package='org.example.app')
    assert result['signature_verified'] and len(result['sha256'])==64 and not result['qualified_release']
    with pytest.raises(GitHubActionsError,match='package ID'):
        inspect_android_zip(package,fmt='apk',variant='debug',expected_package='wrong.example')
    with pytest.raises(GitHubActionsError,match='requires --expected-cert'):
        inspect_android_zip(package,fmt='apk',variant='release')
    with pytest.raises(GitHubActionsError,match='does not match'):
        inspect_android_zip(package,fmt='apk',variant='release',expected_package='org.example.app',expected_cert='f'*64)
    assert inspect_android_zip(package,fmt='apk',variant='release',expected_package='org.example.app',expected_cert=CERT)['certificate_expected']
    duplicate=zipped(tmp_path,['a.apk','b.apk'])
    with pytest.raises(GitHubActionsError,match='exactly one'):
        inspect_android_zip(duplicate,fmt='apk',variant='debug')
    assert inspect_android_zip(duplicate,fmt='apk',variant='debug',member='a.apk')['member']=='a.apk'


def test_android_collect_requires_successful_run_and_workflow(tmp_path):
    class Client:
        def get_run(self,*a):return {'id':33,'status':'in_progress','conclusion':None,'event':'workflow_dispatch','repository':{'full_name':'Owner/repo'}}
        def workflow(self,*a):return {'id':11}
    with pytest.raises(GitHubActionsError,match='successful'):
        android_collect(Client(),'Owner/repo',tmp_path,run_id=33,artifact_id=44,fmt='apk',variant='debug')
    class WrongWorkflow(Client):
        def get_run(self,*a):return {'id':33,'status':'completed','conclusion':'success','event':'workflow_dispatch',
            'repository':{'full_name':'Owner/repo'},'workflow_id':99,'head_sha':SHA,'run_attempt':1}
    with pytest.raises(GitHubActionsError,match='not the selected'):
        android_collect(WrongWorkflow(),'Owner/repo',tmp_path,run_id=33,artifact_id=44,fmt='apk',variant='debug')


def test_android_verify_is_read_only_and_collect_registers_unqualified(tmp_path, monkeypatch):
    project(tmp_path)
    archive=zipped(tmp_path,['some/app-debug.apk'])
    class Client:
        def get_run(self,*a):return {'id':33,'status':'completed','conclusion':'success','event':'workflow_dispatch',
            'repository':{'full_name':'Owner/repo'},'workflow_id':11,'head_sha':SHA,'run_attempt':1}
        def workflow(self,*a):return {'id':11}
    monkeypatch.setattr('devtool.actions_android.verify_download',lambda *a,**kw:{'file':str(archive),'archive_verified':True})
    monkeypatch.setattr('devtool.actions_android._inspect_apk',lambda f:{'package':'org.example.app','version_code':'1',
        'version_name':'1.0','signer_sha256':CERT,'signature_verified':True,'manifest_verified':True})
    out=android_collect(Client(),'Owner/repo',tmp_path,run_id=33,artifact_id=44,fmt='apk',variant='debug',materialize=False)
    assert out['archive_verified'] and 'package_file' not in out
    out=android_collect(Client(),'Owner/repo',tmp_path,run_id=33,artifact_id=44,fmt='apk',variant='debug')
    assert Path(out['package_file']).read_bytes()==b'contents'
    assert out['qualified_release'] is False
    assert (Path(out['package_file']).stat().st_mode & 0o777)==0o600
    from devtool.artifact_registry import load_registry
    items=load_registry(tmp_path)['items']
    assert items[0]['metadata']['qualified_release'] is False
    assert items[0]['metadata']['remote_run_id']==33
    with pytest.raises(GitHubActionsError,match='overwrite'):
        android_collect(Client(),'Owner/repo',tmp_path,run_id=33,artifact_id=44,fmt='apk',variant='debug')
