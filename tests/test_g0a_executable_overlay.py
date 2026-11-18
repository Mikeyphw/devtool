"""Current Milestone A executable ZIP/PYZ security and repository payload tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
import pytest

from devtool.artifacts import validate_artifact, preview_artifact, diff_artifact, read_artifact_manifest
from devtool.runners import _extract_archive
from devtool.executable_overlay import PROFILE, PROFILE_V1, canonical_bootstrap_source


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact(tmp_path: Path, *, suffix: str = '.pyz', profile=True, bootstrap=True,
             corrupt=False, extra=None, entries=None) -> Path:
    raw = {"__main__.py": canonical_bootstrap_source() if profile else b"pass\n", "sample.txt": b"payload\n"}
    if extra:
        raw.update(extra)
    m = {'schema_version':2, 'type':'overlay', 'name':'g0a-test',
         'inventory':{'format':1, 'entries':[{'path':'sample.txt','action':'add',
                    'sha256':sha(raw['sample.txt']), 'size':len(raw['sample.txt'])}]},
         'validation': {'required':True, 'restore':False, 'build':False, 'test':True,
                        'pytest_tests':['tests/test_g0a_executable_overlay.py']}}
    # Synthetic source binding for structural-only tests; native apply separately verifies live SHA.
    m.update(base_contract_mode='exact-tree', base_commit='a'*40, base_tree='b'*40,
             repository={'remote':'git@github.com:Mikeyphw/devtool.git',
                         'branch':'feature/executable-qa','enforce_remote':True,'enforce_branch':True},
             touched_paths=['sample.txt'], source_sha256={'sample.txt':None},
             expected_post_sha256={'sample.txt':sha(raw['sample.txt'])})
    if profile:
        m['executable_overlay']={'profile':PROFILE,'bootstrap':[
            {'path':'__main__.py','sha256':sha(raw['__main__.py']), 'size':len(raw['__main__.py'])+(1 if corrupt else 0)}] if bootstrap else []}
    raw['.devtool-artifact.json'] = json.dumps(m).encode()
    path=tmp_path/('overlay'+suffix)
    with ZipFile(path,'w',compression=ZIP_DEFLATED) as z:
        for name,data in (entries or raw.items()):
            z.writestr(name, data)
    return path


def validated(repo: Path, p: Path):
    return validate_artifact(repo,p,check_repository_identity=False,check_source_state=False)


def test_valid_pyz_excludes_bootstrap_from_inventory_preview_diff_and_extract(tmp_path):
    p=artifact(tmp_path)
    result=validated(tmp_path,p)
    assert result.ok, result.messages
    preview=preview_artifact(tmp_path,p)
    assert preview['files']==1
    assert [x['path'] for x in preview['changes']]==['sample.txt']
    diff=diff_artifact(tmp_path,p)
    assert '__main__.py' not in diff
    dest=tmp_path/'out';dest.mkdir()
    assert _extract_archive(p,dest) >= 2
    assert (dest/'sample.txt').read_text()=='payload\n'
    assert not (dest/'__main__.py').exists()
    assert not (dest/'.devtool-artifact.json').exists()


def test_ordinary_zip_main_is_regular_payload(tmp_path):
    p=artifact(tmp_path,suffix='.zip',profile=False)
    with ZipFile(p,'a') as z:
        manifest=read_artifact_manifest(p)
        manifest['inventory']['entries'].append({'path':'__main__.py','action':'add',
                 'sha256':sha(b'pass\n'),'size':5})
        # Ordinary ZIPs retain the legacy inventory contract without a PYZ source profile.
        for key in ('touched_paths','source_sha256','expected_post_sha256','base_commit','base_tree','base_contract_mode'):
            manifest.pop(key, None)
        # Avoid duplicate manifest by writing a separate clean archive.
    q=tmp_path/'ordinary.zip'
    with ZipFile(q,'w') as z:
        z.writestr('__main__.py','pass\n')
        z.writestr('sample.txt','payload\n')
        z.writestr('.devtool-artifact.json',json.dumps(manifest))
    result=validated(tmp_path,q)
    assert result.ok, result.messages
    dest=tmp_path/'normal';dest.mkdir()
    _extract_archive(q,dest)
    assert (dest/'__main__.py').read_text()=='pass\n'


@pytest.mark.parametrize('kwargs,error',[
    ({'profile':False},'requires explicit'),
    ({'corrupt':True},'hash or size mismatch'),
    ({'bootstrap':False},'bootstrap must declare'),
    ({'extra':{'bootstrap/hidden.py':b'import os\n'}},'Undeclared executable'),
    ({'extra':{'manifest.json':b'{}'}},'Undeclared executable'),
])
def test_invalid_executable_profiles_fail_closed(tmp_path,kwargs,error):
    result=validated(tmp_path,artifact(tmp_path,**kwargs))
    assert not result.ok
    assert error.lower() in ' '.join(result.messages).lower()


def test_reject_symlink_and_unsafe_paths_and_aliases(tmp_path):
    for idx,bad in enumerate(['../escape','/absolute','./sample.txt']):
        with ZipFile(tmp_path/('mal'+str(idx)+'.pyz'),'w') as z:
            z.writestr(bad,b'bad')
            z.writestr('__main__.py','pass\n')
        assert not validated(tmp_path,tmp_path/('mal'+str(idx)+'.pyz')).ok
    p=tmp_path/'duplicates.pyz'
    with ZipFile(p,'w') as z:
        z.writestr('__main__.py','pass\n');z.writestr('__main__.py','pass\n')
    assert not validated(tmp_path,p).ok
    p=tmp_path/'symlink.pyz'
    with ZipFile(p,'w') as z:
        zi=ZipInfo('__main__.py');zi.create_system=3;zi.external_attr=(stat.S_IFLNK | 0o777)<<16
        z.writestr(zi,'/tmp')
    assert not validated(tmp_path,p).ok


def test_budget_bomb_is_rejected_before_uncompressing(tmp_path):
    p=artifact(tmp_path,extra={'bootstrap/bomb.py': b'a' * 200000})
    result=validated(tmp_path,p)
    assert not result.ok
    assert 'decompression budget' in ' '.join(result.messages).lower()


def test_wrong_profile_and_metadata_overlap(tmp_path):
    p=artifact(tmp_path)
    data=read_artifact_manifest(p)
    data['executable_overlay']['profile']='other-profile'
    q=tmp_path/'wrong.pyz'
    with ZipFile(q,'w') as z:
        z.writestr('.devtool-artifact.json',json.dumps(data))
        z.writestr('__main__.py',canonical_bootstrap_source());z.writestr('sample.txt','payload\n')
    assert not validated(tmp_path,q).ok
    data['executable_overlay']['profile']=PROFILE
    data['inventory']['entries'].append({'path':'__main__.py','action':'add','size':len(canonical_bootstrap_source()),'sha256':sha(canonical_bootstrap_source())})
    r=tmp_path/'overlap.pyz'
    with ZipFile(r,'w') as z:
        z.writestr('.devtool-artifact.json',json.dumps(data))
        z.writestr('__main__.py',canonical_bootstrap_source());z.writestr('sample.txt','payload\n')
    assert not validated(tmp_path,r).ok


def test_bootstrap_directory_marker_not_materialized(tmp_path):
    raw={'__main__.py':canonical_bootstrap_source(PROFILE_V1),'bootstrap/loader.py':b'pass\n','sample.txt':b'payload\n'}
    m={'schema_version':2,'type':'overlay',
       'validation':{'required':True,'test':True},
       'inventory':{'format':1,'entries':[{'path':'sample.txt','action':'add',
                    'size':len(raw['sample.txt']),'sha256':sha(raw['sample.txt'])}]},
       'executable_overlay':{'profile':PROFILE_V1, 'bootstrap':[
           {'path':p,'size':len(raw[p]),'sha256':sha(raw[p])} for p in ('__main__.py','bootstrap/loader.py')]}}
    p=tmp_path/'with-dir.pyz'
    with ZipFile(p,'w') as z:
        z.writestr('bootstrap/',b'')
        for name,data in raw.items(): z.writestr(name,data)
        z.writestr('.devtool-artifact.json',json.dumps(m))
    assert validated(tmp_path,p).ok
    preview=preview_artifact(tmp_path,p)
    assert [change['path'] for change in preview['changes']]==['sample.txt']
    dest=tmp_path/'out-dir'; dest.mkdir()
    _extract_archive(p,dest)
    assert not (dest/'bootstrap').exists()


def test_null_profile_is_not_ordinary_zip(tmp_path):
    p=artifact(tmp_path,suffix='.zip')
    m=read_artifact_manifest(p)
    m['executable_overlay']=None
    q=tmp_path/'null.zip'
    with ZipFile(q,'w') as z:
        z.writestr('.devtool-artifact.json',json.dumps(m))
        z.writestr('__main__.py',canonical_bootstrap_source(PROFILE_V1))
        z.writestr('sample.txt',b'payload\n')
    assert not validated(tmp_path,q).ok


def test_profile_cannot_skip_validation(tmp_path):
    p=artifact(tmp_path)
    m=read_artifact_manifest(p)
    m['validation']={'required':False,'restore':False,'build':False,'test':False}
    q=tmp_path/'no-validation.pyz'
    with ZipFile(q,'w') as z:
        z.writestr('.devtool-artifact.json',json.dumps(m))
        z.writestr('__main__.py',canonical_bootstrap_source(PROFILE_V1))
        z.writestr('sample.txt',b'payload\n')
    assert not validated(tmp_path,q).ok


def test_direct_pyz_bootstrap_is_native_cli_only_and_requires_approval(tmp_path):
    import subprocess, sys, os
    p=artifact(tmp_path)
    env=dict(os.environ)
    # Executing a genuine PYZ invokes only native Devtool CLI. Without --repo
    # or explicit --yes, it must not apply or modify any source.
    no_repo=subprocess.run([sys.executable, str(p), 'check'],
                           capture_output=True, text=True, env=env)
    assert no_repo.returncode != 0
    assert 'required' in no_repo.stderr.lower()
    no_auth=subprocess.run([sys.executable, str(p), 'apply', '--repo', str(tmp_path)],
                           capture_output=True, text=True, env=env)
    assert no_auth.returncode != 0
    assert 'requires explicit --yes' in no_auth.stderr


def test_direct_bootstrap_cannot_import_shadowed_argparse_payload(tmp_path):
    import os, subprocess, sys
    marker=tmp_path/'SHADOW_IMPORT_EXECUTED'
    fake=(f"open({str(marker)!r},'w').write('pwned')\n").encode()
    raw={'__main__.py':canonical_bootstrap_source(PROFILE_V1),'argparse.py':fake,
         'sample.txt':b'payload\n'}
    m={'schema_version':2,'type':'overlay',
       'validation':{'required':True,'test':True},
       'inventory':{'format':1,'entries':[{'path':p,'action':'add',
           'size':len(raw[p]),'sha256':sha(raw[p])} for p in ('argparse.py','sample.txt')]},
       'executable_overlay':{'profile':PROFILE_V1,'bootstrap':[
           {'path':'__main__.py','size':len(raw['__main__.py']),
            'sha256':sha(raw['__main__.py'])}]}}
    p=tmp_path/'shadow.pyz'
    with ZipFile(p,'w') as z:
        for name,data in raw.items():z.writestr(name,data)
        z.writestr('.devtool-artifact.json',json.dumps(m))
    assert validated(tmp_path,p).ok
    done=subprocess.run([sys.executable,str(p),'--help'],capture_output=True,text=True,
                        env=dict(os.environ))
    assert done.returncode==0,done.stderr
    assert not marker.exists()


def _repackage(original: Path, destination: Path, update):
    with ZipFile(original) as z:
        raw={name:z.read(name) for name in z.namelist()}
    manifest=json.loads(raw['.devtool-artifact.json'])
    update(manifest,raw)
    raw['.devtool-artifact.json']=json.dumps(manifest).encode()
    with ZipFile(destination,'w',compression=ZIP_DEFLATED) as z:
        for name,data in raw.items():
            z.writestr(name,data)
    return destination


def test_v2_exact_source_binding_is_mandatory(tmp_path):
    original=artifact(tmp_path)
    for key in ('base_commit','base_tree','touched_paths','source_sha256',
                'expected_post_sha256','repository'):
        changed=_repackage(original,tmp_path/(key+'.pyz'),
                           lambda manifest,raw: manifest.pop(key))
        result=validated(tmp_path,changed)
        assert not result.ok, key
        assert 'v2' in ' '.join(result.messages).lower(), result.messages


def test_v2_refuses_forged_post_inventory(tmp_path):
    original=artifact(tmp_path)
    changed=_repackage(original,tmp_path/'bad-post.pyz',
                       lambda m,raw: m['expected_post_sha256'].update({'sample.txt':'f'*64}))
    result=validated(tmp_path,changed)
    assert not result.ok
    assert 'source/post hash inventory' in ' '.join(result.messages)


def test_v1_immutable_bootstrap_remains_accepted(tmp_path):
    from devtool.executable_overlay import PROFILE_V1, BOOTSTRAP_V1, BOOTSTRAP_V2
    assert canonical_bootstrap_source(PROFILE_V1) == BOOTSTRAP_V1
    assert BOOTSTRAP_V1 != BOOTSTRAP_V2
    assert b'adopt-committed' not in BOOTSTRAP_V1
    assert b'adopt-committed' in BOOTSTRAP_V2
    original=artifact(tmp_path)
    def downgrade(m,raw):
        m['executable_overlay']['profile']=PROFILE_V1
        raw['__main__.py']=canonical_bootstrap_source(PROFILE_V1)
        m['executable_overlay']['bootstrap'][0]['size']=len(raw['__main__.py'])
        m['executable_overlay']['bootstrap'][0]['sha256']=sha(raw['__main__.py'])
        for key in ('base_commit','base_tree','base_contract_mode','touched_paths',
                    'source_sha256','expected_post_sha256','repository'):
            m.pop(key,None)
    old=_repackage(original,tmp_path/'legacy.pyz',downgrade)
    result=validated(tmp_path,old)
    assert result.ok, result.messages


def test_executable_profile_allows_explicit_owned_validation_metadata(tmp_path):
    original=artifact(tmp_path)
    path='.devtool-artifact/validation/protocol.txt'
    payload=b'owned by the destination repo\n'
    def add(m,raw):
        raw[path]=payload
        m['inventory']['entries'].append({'path':path,'action':'add','sha256':sha(payload),'size':len(payload)})
        m['touched_paths']=sorted([*m['touched_paths'],path])
        m['source_sha256'][path]=None
        m['expected_post_sha256'][path]=sha(payload)
    p=_repackage(original,tmp_path/'validation-payload.pyz',add)
    result=validated(tmp_path,p)
    assert result.ok, result.messages
    dest=tmp_path/'owned-payload';dest.mkdir()
    _extract_archive(p,dest)
    assert (dest/path).read_bytes()==payload
    assert not (dest/'__main__.py').exists()
