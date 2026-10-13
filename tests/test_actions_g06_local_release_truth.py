"""G6 provenance is a conjunction, never local-signature -> qualified-release promotion."""
import copy
import hashlib
from pathlib import Path

import pytest

from devtool.actions_local_evidence import signed_local_evidence
from devtool.github_actions import GitHubActionsError
from devtool.cli import build_parser
from devtool.core_registry import build_core_registry

SHA = 'a' * 40
CERT = 'b' * 64
SIGNED = '1' * 12
UNSIGNED = '2' * 12
APP = 'io.github.mikeceead.devtoolhud'


def h(data): return hashlib.sha256(data).hexdigest()


def setup(tmp_path, monkeypatch, fmt='apk'):
    import devtool.actions_local_evidence as module
    repo = tmp_path / 'devtool'
    source = repo / '.devtool' / 'artifacts'
    folder = 'android-locally-signed' if fmt == 'apk' else 'android-aab-signed'
    dst = source / folder
    dst.mkdir(parents=True)
    data = b'verified-signed-' + fmt.encode()
    signed = dst / ('out.' + fmt)
    signed.write_bytes(data)
    unsigned = repo / 'unsigned.candidate'
    unsigned.write_bytes(b'unsigned bytes')
    workflow = 'devtool-android-unsigned-release.yml' if fmt == 'apk' else 'devtool-android-aab-candidate.yml'
    path = '.github/workflows/' + workflow
    meta = {'producer':'devtool-native-life',
            'state':'locally-signed-unqualified' if fmt=='apk' else 'locally-signed-aab-unqualified',
            'signature_verified':True, 'locally_signed':True, 'qualified_release':False,
            'remote_repository':'Mikeyphw/devtool', 'remote_workflow_path':path,
            'remote_run_id':22, 'remote_run_attempt':2,'remote_head_sha':SHA,
            'signer_sha256':CERT,'signing_identity_id':'private-life',
            'unsigned_registry_id':UNSIGNED,'signed_sha256':h(data),
            'format':fmt}
    if fmt=='apk':
        meta.update(unsigned_sha256=h(unsigned.read_bytes()),package=APP,version_code='31')
    original = {'remote_repository':'Mikeyphw/devtool', 'remote_workflow_path':path,
                'remote_run_id':22, 'remote_run_attempt':2,'remote_head_sha':SHA,
                'archive_verified':True,'qualified_release':False,'signature_verified':False,
                'package':APP,'version_code':'31','unit_tests_verified':True,'lint_verified':True}
    rows = [dict(id=SIGNED, path=str(signed.relative_to(repo)),size=len(data),sha256=h(data),metadata=meta),
            dict(id=UNSIGNED,metadata=original)]
    monkeypatch.setattr(module,'load_registry',lambda _: {'items':rows})
    monkeypatch.setattr(module,'imported_apk',lambda root,rid: (unsigned,original))
    monkeypatch.setattr(module,'imported_aab',lambda root,rid: (unsigned,original))
    def fake_verify(root, **kwargs):
        assert kwargs['identity_id']=='private-life'
        assert kwargs['application_id']==APP
        assert kwargs['minimum_version_code']==31
        return {'artifact_sha256':h(signed.read_bytes()),'signer_sha256':CERT,
                'application_id':APP,'signing_identity_id':'private-life',
                'local_signature_verified':True,'qualified_release':False,'version_code':31}
    monkeypatch.setattr('devtool.actions_local_release.local_verify',fake_verify)
    return repo, signed, rows, original, unsigned


def inspect(repo,fmt='apk',**kwargs):
    return signed_local_evidence(repo,registry_id=SIGNED,fmt=fmt,expected_source_sha=SHA,**kwargs)


@pytest.mark.parametrize('fmt',['apk','aab'])
def test_read_only_verified_lineage_never_upgrades_release(tmp_path,monkeypatch,fmt):
    root,_,rows,_,_=setup(tmp_path,monkeypatch,fmt)
    got=inspect(root,fmt)
    assert got['read_only'] is True
    assert got['registered_artifact_lineage_verified'] and got['local_signature_verified']
    assert got['qualified_release'] is False and got['source_attested'] is False
    assert got['github_provenance_verified'] is False and got['published'] is False
    assert got['remote_run_attempt']==2 and got['unsigned_registry_id']==UNSIGNED
    assert got['remote_source_sha']==SHA
    assert len(rows)==2


@pytest.mark.parametrize('field,value',[
    ('remote_run_attempt',3),('remote_run_id',99),('remote_head_sha','c'*40),
    ('remote_workflow_path','.github/workflows/wrong.yml'),
    ('qualified_release',True),('signature_verified',False),
    ('signer_sha256','f'*64),('unsigned_registry_id','3'*12),
    ('remote_repository','Other/project'),('state','protected-release')
])
def test_rejects_tampered_signed_metadata(tmp_path,monkeypatch,field,value):
    root,_,rows,_,_=setup(tmp_path,monkeypatch)
    rows[0]['metadata'][field]=value
    with pytest.raises(GitHubActionsError):inspect(root)


@pytest.mark.parametrize('field,value',[
    ('remote_run_attempt',3),('remote_run_id',23),('remote_head_sha','c'*40),
    ('archive_verified',False),('signature_verified',True),
    ('remote_workflow_path','.github/workflows/other.yml')
])
def test_rejects_unsigned_parent_conflicts(tmp_path,monkeypatch,field,value):
    root,_,_,parent,_=setup(tmp_path,monkeypatch)
    parent[field]=value
    with pytest.raises(GitHubActionsError,match='conflicting provenance'):
        inspect(root)


def test_rejects_tampered_file_and_metadata_digest(tmp_path,monkeypatch):
    root,path,rows,_,_=setup(tmp_path,monkeypatch)
    path.write_bytes(b'different bytes')
    with pytest.raises(GitHubActionsError):inspect(root)
    path.write_bytes(b'verified-signed-apk')
    rows[0]['metadata']['signed_sha256']='f'*64
    with pytest.raises(GitHubActionsError):inspect(root)


def test_rejects_aliases_symlinks_and_unknown_registry(tmp_path,monkeypatch):
    root,path,rows,_,_=setup(tmp_path,monkeypatch)
    with pytest.raises(GitHubActionsError,match='registry ID'):
        signed_local_evidence(root,registry_id='unknown')
    rows.append(copy.deepcopy(rows[0]))
    with pytest.raises(GitHubActionsError,match='ambiguous'):
        inspect(root)
    rows.pop()
    path.rename(path.with_suffix('.moved'))
    path.symlink_to(path.with_suffix('.moved'))
    with pytest.raises(GitHubActionsError,match='symlink|path'):
        inspect(root)


def test_rejects_wrong_expected_identity_sha_and_false_independent_verifier(tmp_path,monkeypatch):
    root,path,rows,_,_=setup(tmp_path,monkeypatch)
    with pytest.raises(GitHubActionsError,match='source'):
        signed_local_evidence(root,registry_id=SIGNED,expected_source_sha='c'*40)
    with pytest.raises(GitHubActionsError,match='LIFE'):
        inspect(root,identity_id='unrelated')
    monkeypatch.setattr('devtool.actions_local_release.local_verify',lambda *a,**kw: {
        'artifact_sha256':h(path.read_bytes()),'signer_sha256':CERT,
        'application_id':APP,'signing_identity_id':'private-life',
        'local_signature_verified':True,'qualified_release':True,'version_code':31})
    with pytest.raises(GitHubActionsError,match='inspection conflicts'):
        inspect(root)


def test_aab_requires_parent_test_lint_evidence(tmp_path,monkeypatch):
    root,_,_,parent,_=setup(tmp_path,monkeypatch,'aab')
    parent['lint_verified']=False
    with pytest.raises(GitHubActionsError,match='test/lint'):
        inspect(root,'aab')


def test_command_schema_and_explicit_receipt_id():
    args=build_parser().parse_args('actions release local-evidence --registry-id 26529ad5f699 --format aab --json'.split())
    assert args.release_command=='local-evidence' and args.registry_id=='26529ad5f699' and args.format=='aab'
    assert build_core_registry().resolve_command(['actions','release','local-evidence'])['command'].stable_id == 'cli.command.actions.release.local_evidence'
