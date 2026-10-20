"""R03: Android role/workflow provenance, no CI promotion or device mutation."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from devtool import actions_android as aa
from devtool.cli import build_parser
from devtool.github_actions import GitHubActionsError

CERT = 'b' * 64
SHA = 'a' * 40
BADGING_APP = "package: name='org.example.app' versionCode='2' versionName='2.0'\n"
BADGING_TEST = "package: name='org.example.app.test' versionCode='' versionName=''\n"
XML_TEST = '''E: manifest (line=2)
  A: package="org.example.app.test" (Raw: "org.example.app.test")
  E: application (line=4)
  E: instrumentation (line=6)
    A: android:name(0x01010003)="androidx.test.runner.AndroidJUnitRunner" (Raw: "androidx.test.runner.AndroidJUnitRunner")
    A: android:targetPackage(0x01010021)="org.example.app" (Raw: "org.example.app")
'''


def mock_tools(monkeypatch, badging=BADGING_TEST, xml=XML_TEST, signer=CERT, *, aapt2=False):
    def _tool(name):
        if name == 'aapt' and aapt2:
            raise GitHubActionsError('aapt missing')
        return name
    commands = []
    def _run(argv, *, purpose):
        commands.append(argv)
        if argv[0] == 'apksigner':
            return 'Signer #1 certificate SHA-256 digest: '+signer
        if 'badging' in argv:
            return badging
        if 'xmltree' in argv:
            return xml
        raise AssertionError(argv)
    monkeypatch.setattr(aa, '_tool', _tool)
    monkeypatch.setattr(aa, '_run', _run)
    return commands


def archive(tmp_path, filename='test.apk'):
    f = tmp_path / 'artifacts.zip'
    with zipfile.ZipFile(f, 'w') as z:
        z.writestr(filename, b'placeholder signed APK, tools are mocked')
    return f


def inspect_test(tmp_path, **kwargs):
    return aa.inspect_android_zip(archive(tmp_path), fmt='apk', variant='debug',
        expected_package='org.example.app.test', apk_role='instrumentation',
        expected_target_package='org.example.app', **kwargs)


def test_app_version_must_be_positive_number_even_for_debug(monkeypatch, tmp_path):
    for version in ('', '0', 'bogus', '-1', '01x'):
        mock_tools(monkeypatch, BADGING_APP.replace("versionCode='2'", f"versionCode='{version}'"))
        with pytest.raises(GitHubActionsError, match='positive numeric'):
            aa.inspect_android_zip(archive(tmp_path), fmt='apk', variant='debug',expected_package='org.example.app')
    mock_tools(monkeypatch, BADGING_APP)
    assert aa.inspect_android_zip(archive(tmp_path),fmt='apk',variant='debug')['version_code']=='2'


def test_instrumentation_without_fake_version_allowed(monkeypatch, tmp_path):
    commands=mock_tools(monkeypatch)
    out=inspect_test(tmp_path)
    assert out['version_code']=='' and out['version_name']==''
    assert out['apk_role']=='instrumentation'
    assert out['instrumentation_target_package']=='org.example.app'
    assert out['instrumentation_runner']=='androidx.test.runner.AndroidJUnitRunner'
    assert out['signature_verified'] is True and out['qualified_release'] is False
    assert any(x[:3]==['aapt','dump','xmltree'] for x in commands)


def test_aapt2_supported(monkeypatch,tmp_path):
    calls=mock_tools(monkeypatch,aapt2=True)
    assert inspect_test(tmp_path)['instrumentation_target_package']=='org.example.app'
    assert any(x[:5]==['aapt2','dump','xmltree','--file','AndroidManifest.xml'] for x in calls)


def test_test_role_requires_identity_pins_and_disallows_release_aab(tmp_path):
    p=archive(tmp_path)
    for opts in [dict(fmt='apk',variant='release',expected_package='org.example.app.test',expected_target_package='org.example.app'),
                 dict(fmt='aab',variant='debug',expected_package='org.example.app.test',expected_target_package='org.example.app'),
                 dict(fmt='apk',variant='debug',expected_package='org.example.app.test'),
                 dict(fmt='apk',variant='debug',expected_target_package='org.example.app')]:
        with pytest.raises(GitHubActionsError):aa.inspect_android_zip(p,apk_role='instrumentation',**opts)
    with pytest.raises(GitHubActionsError,match='cannot accept instrumentation'):
        aa.inspect_android_zip(p,fmt='apk',variant='debug',expected_target_package='org.example.app')
    with pytest.raises(GitHubActionsError,match='Unknown APK role'):
        aa.inspect_android_zip(p,fmt='apk',variant='debug',apk_role='stealth')


@pytest.mark.parametrize('badging',[
    BADGING_TEST+BADGING_TEST,
    "package: name='org.example.app.test' versionCode='' versionName='' versionCode='3'",
    "package: name='org.example.app.test' versionCode='x' versionName=''",
    "package: name='org.example.app.test' versionCode='0' versionName=''",
    "package: name='org.example.app.test' versionCode=''",
    "package: name='org.example.app' versionCode='' versionName=''",
])
def test_refuses_ambiguous_test_apk_badging(monkeypatch,tmp_path,badging):
    mock_tools(monkeypatch,badging)
    with pytest.raises(GitHubActionsError):inspect_test(tmp_path)


@pytest.mark.parametrize('xml',[
    XML_TEST.replace('org.example.app" (Raw:', 'org.wrong.target" (Raw:'),
    XML_TEST.replace('A: package="org.example.app.test"', 'A: package="org.other.app"'),
    XML_TEST.replace('  E: instrumentation (line=6)', '  E: activity (line=6)'),
    XML_TEST+'  E: instrumentation (line=9)\n    A: android:targetPackage="org.example.app"',
    XML_TEST.replace('android:name(0x01010003)', 'android:label(0x01010003)'),
    XML_TEST.replace('android:targetPackage(0x01010021)', 'android:other(0x01010021)'),
])
def test_compiled_manifest_denial(monkeypatch,tmp_path,xml):
    mock_tools(monkeypatch,xml=xml)
    with pytest.raises(GitHubActionsError):inspect_test(tmp_path)


def test_duplicate_instrumentation_attributes_denied(monkeypatch,tmp_path):
    xml=XML_TEST+'    A: android:targetPackage(0x01010021)="org.example.app"\n'
    mock_tools(monkeypatch,xml=xml)
    with pytest.raises(GitHubActionsError,match='duplicate'):inspect_test(tmp_path)


def test_bad_certificate_fails_closed(monkeypatch,tmp_path):
    mock_tools(monkeypatch,signer='xx')
    with pytest.raises(GitHubActionsError,match='fingerprint'):inspect_test(tmp_path)


class Client:
    def __init__(self,workflow_id=11,head_sha=SHA,attempt=2,success=True):
        self.workflow_id=workflow_id;self.head_sha=head_sha;self.attempt=attempt;self.success=success;self.calls=[]
    def get_run(self,slug,rid):
        self.calls.append(('run',slug,rid))
        return {'id':rid,'status':'completed' if self.success else 'in_progress',
            'conclusion':'success' if self.success else None,'event':'workflow_dispatch',
            'repository':{'full_name':slug}, 'workflow_id':self.workflow_id,
            'head_sha':self.head_sha,'run_attempt':self.attempt}
    def workflow(self,slug,name):
        self.calls.append(('workflow',slug,name))
        return {'id':11}


def collect(c,root,**kw):
    options = dict(workflow='skybind-android-apks.yml',expected_source_sha=SHA,
        expected_run_attempt=2,apk_role='instrumentation',
        expected_package='org.example.app.test',expected_target_package='org.example.app',
        materialize=False)
    options.update(kw)
    return aa.android_collect(c,'Owner/repo',root,run_id=25,artifact_id=34,
        fmt='apk',variant='debug',**options)


def test_custom_workflow_exact_identity_and_source(monkeypatch,tmp_path):
    monkeypatch.setattr(aa,'verify_download',lambda *a,**kw:{'file':str(archive(tmp_path))})
    mock_tools(monkeypatch)
    c=Client()
    out=collect(c,tmp_path)
    assert c.calls[-1]==('workflow','Owner/repo','skybind-android-apks.yml')
    assert out['remote_head_sha']==SHA and out['remote_attempt']==2
    assert out['remote_workflow_id']==11 and out['workflow']=='skybind-android-apks.yml'
    assert not (tmp_path/'.devtool').exists()


def test_custom_workflow_identity_denials_before_download(monkeypatch,tmp_path):
    called=[]
    monkeypatch.setattr(aa,'verify_download',lambda *a,**kw:called.append('download'))
    for c in [Client(workflow_id=12),Client(head_sha='f'*40),Client(attempt=3),Client(success=False)]:
        with pytest.raises(GitHubActionsError): collect(c,tmp_path)
    assert called==[]
    for bad in ['../steal.yml','https://example.net/workflow.yml','x.yml/../a.yml','workflow.yaml#x','']:
        with pytest.raises(GitHubActionsError):collect(Client(),tmp_path,workflow=bad)
    with pytest.raises(GitHubActionsError,match='requires --expected-source-sha'):
        aa.android_collect(Client(),'Owner/repo',tmp_path,run_id=25,artifact_id=34,fmt='apk',variant='debug',
            workflow='skybind-android-apks.yml',expected_run_attempt=2)
    with pytest.raises(GitHubActionsError,match='requires --expected-source-sha'):
        aa.android_collect(Client(),'Owner/repo',tmp_path,run_id=25,artifact_id=34,fmt='apk',variant='debug',
            workflow='skybind-android-apks.yml',expected_source_sha=SHA)


def test_cli_args_are_first_class_and_no_mutation():
    parser=build_parser()
    for op in ('verify','collect'):
        args=parser.parse_args(['actions','android',op,'--run-id','25','--artifact-id','34',
            '--workflow','skybind-android-apks.yml','--apk-role','instrumentation',
            '--expected-package','org.example.app.test','--expected-target-package','org.example.app',
            '--expected-source-sha',SHA,'--expected-run-attempt','2'])
        assert args.workflow=='skybind-android-apks.yml' and args.apk_role=='instrumentation'
        assert args.expected_run_attempt==2 and args.expected_source_sha==SHA
