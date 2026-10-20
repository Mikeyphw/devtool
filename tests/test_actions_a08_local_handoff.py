"""A08 offline positive/negative tests; never invoke real adb or mutate real devices."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool.actions_private_handoff import private_handoff
from devtool.github_actions import GitHubActionsError
from devtool.cli import build_parser
from devtool.core_registry import build_core_registry

SHA = 'a' * 40
CERT = 'b' * 64
SIGNED = '1' * 12
DATA = b'fake-verified-APK-payload'


def h(b): return hashlib.sha256(b).hexdigest()


def setup(tmp_path, monkeypatch, fmt='apk'):
    root = tmp_path / 'repo'
    dst = root / '.devtool' / 'artifacts' / ('android-locally-signed' if fmt=='apk' else 'android-aab-signed')
    dst.mkdir(parents=True)
    p = dst / ('signed.'+fmt)
    p.write_bytes(DATA)
    import devtool.actions_private_handoff as module
    monkeypatch.setattr('devtool.artifact_registry.load_registry',lambda r: {'items':[{'id':SIGNED,'path':str(p.relative_to(root))}]})
    proof = {'signed_sha256':h(DATA),'signed_registry_id':SIGNED, 'source_attested':False,
             'qualified_release':False, 'local_signature_verified':True,'unsigned_registry_id':'2'*12}
    def verified(r,**kw):
        assert r==root.resolve()
        assert kw['expected_source_sha']==SHA
        assert kw['registry_id']==SIGNED
        return dict(proof)
    monkeypatch.setattr(module,'signed_local_evidence',verified)
    return root,p


def call(root,**kw):
    args=dict(registry_id=SIGNED,operation='export',fmt='apk',expected_source_sha=SHA,
              destination='',serial='',yes=False,allow_unqualified=False)
    args.update(kw)
    return private_handoff(root,**args)


def test_canonical_command_registration():
    c=('actions release local-handoff --registry-id '+SIGNED+
       ' --expected-sha '+SHA+' --operation export --destination /tmp/example.apk --json')
    a=build_parser().parse_args(c.split())
    assert a.release_command=='local-handoff' and a.operation=='export' and not a.yes
    assert build_core_registry().resolve_command(['actions','release','local-handoff'])['command'].stable_id.endswith('local_handoff')


def test_signed_unqualified_preview_never_mutates(tmp_path,monkeypatch):
    root,_=setup(tmp_path,monkeypatch)
    dest=tmp_path/'out.apk'
    result=call(root,destination=str(dest),allow_unqualified=True)
    assert not dest.exists()
    assert result['status']=='signed-but-unqualified' and not result['executed']
    for k in ('source_attested','qualified_release','device_qualified','published'):
        assert result[k] is False


def test_consent_denied_even_with_yes(tmp_path,monkeypatch):
    root,_=setup(tmp_path,monkeypatch)
    with pytest.raises(GitHubActionsError,match='allow-unqualified'):
        call(root,destination=str(tmp_path/'out.apk'),yes=True)
    assert not (tmp_path/'out.apk').exists()


def test_export_snapshots_verified_bytes_and_never_overwrites(tmp_path,monkeypatch):
    root,_=setup(tmp_path,monkeypatch)
    dest=tmp_path/'out.apk'
    result=call(root,destination=str(dest),yes=True,allow_unqualified=True)
    assert result['executed'] and result['qualified_release'] is False
    assert dest.read_bytes()==DATA and dest.stat().st_mode & 0o077 == 0
    with pytest.raises(GitHubActionsError,match='already exists'):
        call(root,destination=str(dest),yes=True,allow_unqualified=True)
    assert dest.read_bytes()==DATA


def test_refuse_symlink_destination_wrong_sha_and_relative_export(tmp_path,monkeypatch):
    root,_=setup(tmp_path,monkeypatch)
    dest=tmp_path/'out.apk';dest.symlink_to(tmp_path/'elsewhere')
    with pytest.raises(GitHubActionsError):call(root,destination=str(dest))
    with pytest.raises(GitHubActionsError):call(root,destination='relative.apk')
    with pytest.raises(GitHubActionsError,match='expected-sha'):
        call(root,destination=str(tmp_path/'fresh.apk'),expected_source_sha='b'*39)


def test_refuse_source_changes_between_verify_and_export(tmp_path,monkeypatch):
    root,path=setup(tmp_path,monkeypatch)
    import devtool.actions_private_handoff as module
    original=module._stored_file
    def mutate(*a):
        path.write_bytes(b'tampered package')
        return original(*a)
    monkeypatch.setattr(module,'_stored_file',mutate)
    dest=tmp_path/'out.apk'
    with pytest.raises(GitHubActionsError,match='changed'):
        call(root,destination=str(dest),yes=True,allow_unqualified=True)
    assert not dest.exists()


def test_apk_install_requires_exact_explicit_serial_and_preflight(tmp_path,monkeypatch):
    root,_=setup(tmp_path,monkeypatch)
    with pytest.raises(GitHubActionsError,match='serial'):
        call(root,operation='install')
    with pytest.raises(GitHubActionsError,match='serial'):
        call(root,operation='install',serial='emulator; rm -rf /')
    with pytest.raises(GitHubActionsError,match='only APK'):
        call(root,operation='install',fmt='aab',serial='emulator-5554')
    requests=[]
    monkeypatch.setattr('devtool.actions_private_handoff.shutil.which',lambda k:'/usr/bin/adb')
    def fake_run(argv,**kwargs):
        requests.append(list(argv))
        if argv[-1]=='get-state':return SimpleNamespace(returncode=0,stdout='device\n',stderr='')
        assert argv[-2]=='install'
        assert Path(argv[-1]).read_bytes()==DATA
        return SimpleNamespace(returncode=0,stdout='Success\n',stderr='')
    monkeypatch.setattr('devtool.actions_private_handoff.subprocess.run',fake_run)
    preview=call(root,operation='install',serial='emulator-5554')
    assert not requests and not preview['executed']
    result=call(root,operation='install',serial='emulator-5554',yes=True,allow_unqualified=True)
    assert result['device_install_reported'] and result['device_qualified'] is False
    assert len(requests)==2 and requests[0][:3]==['/usr/bin/adb','-s','emulator-5554']
    assert requests[1][3]=='install'


def test_adb_failure_never_qualifies_device(tmp_path,monkeypatch):
    root,_=setup(tmp_path,monkeypatch)
    monkeypatch.setattr('devtool.actions_private_handoff.shutil.which',lambda n:'/usr/bin/adb')
    monkeypatch.setattr('devtool.actions_private_handoff.subprocess.run',lambda a,**k:SimpleNamespace(returncode=1,stdout='',stderr='offline'))
    with pytest.raises(GitHubActionsError,match='unavailable'):
        call(root,operation='install',serial='ABCDEF123',yes=True,allow_unqualified=True)


def test_race_in_original_unsigned_parent_verification_is_rejected(tmp_path,monkeypatch):
    # Import existing G6 full registry fixture, and mutate unsigned bytes during
    # independent LIFE signer check. Hash must compare against the earlier bytes.
    import importlib.util
    source=Path(__file__).with_name('test_actions_g06_local_release_truth.py')
    spec=importlib.util.spec_from_file_location('g6_fixture_for_a08',source)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    root,_,_,_,unsigned=mod.setup(tmp_path,monkeypatch)
    from devtool.actions_local_evidence import signed_local_evidence
    def changed_verify(*a,**kw):
        unsigned.write_bytes(b'changed while signing inspector runs')
        return {'artifact_sha256':mod.h(b'verified-signed-apk'),'signer_sha256':mod.CERT,
                'application_id':mod.APP,'signing_identity_id':'private-life',
                'local_signature_verified':True,'qualified_release':False,'version_code':31}
    monkeypatch.setattr('devtool.actions_local_release.local_verify',changed_verify)
    with pytest.raises(GitHubActionsError,match='changed during independent inspection'):
        signed_local_evidence(root,registry_id=mod.SIGNED,expected_source_sha=mod.SHA)


def test_action_adapter_routes_to_local_without_github_auth(tmp_path,monkeypatch):
    from devtool.github_actions import actions_cli
    root,_ = setup(tmp_path,monkeypatch)
    args=build_parser().parse_args(('actions release local-handoff --registry-id '+SIGNED+
        ' --operation export --destination '+str(tmp_path/'out.apk')+' --expected-sha '+SHA+' --json').split())
    got=actions_cli(args,root)
    assert got['qualified_release'] is False and got['executed'] is False
    assert not (tmp_path/'out.apk').exists()


def test_a08_g8_amendment_is_pinned_and_never_qualifies_release(tmp_path):
    from devtool.actions_qualification import qualification_report, _a08_g6_amendment_valid
    import importlib.util,shutil,json
    source=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('g8_fixture_for_a08', source/'tests/test_actions_g08_qualification.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    root=mod.fixture(tmp_path)
    extra=('docs/actions/ACTIONS_A08_PRIVATE_HANDOFF.md','tests/test_actions_a08_local_handoff.py')
    for name in extra:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/name,p)
    assert _a08_g6_amendment_valid(root)
    report=qualification_report(root)  # G2 has a pre-existing unrelated frozen pin mismatch; do not waive it.
    assert report['qualified_release'] is False and report['source_attested'] is False
    assert any(a['wave']=='G6' and a['status']=='VERIFIED_POST_G08_AMENDMENT'
               for a in report['verified_source_amendments'])
    (root/extra[0]).write_bytes(b'tampered-A08-doc')
    assert not _a08_g6_amendment_valid(root)
    report=qualification_report(root)
    assert any(row.get('wave')=='G6' and row.get('status')=='SOURCE_PINS_MISMATCH'
               for row in report['source_contracts'])
