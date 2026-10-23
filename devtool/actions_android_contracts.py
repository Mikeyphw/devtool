"""Explicit reviewed Android consumer/variant contracts, never inferred release authority.

The consumer registry is local Devtool policy. It contains no signing secrets.
Register external producers by exact repository/workflow/artifact and the LIFE identity
already authorized for their package. A missing or ambiguous entry fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import hashlib
import re
import zipfile

from .github_actions import GitHubActionsError

_REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
_FILE = re.compile(r'[A-Za-z0-9_.-]+\Z')
_ID = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\Z')
_VARIANT = re.compile(r'[a-z][A-Za-z0-9]{0,60}\Z')
_MEMBER = re.compile(r'[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*\Z')
_HUD = dict(id='devtool-hud', repository='Mikeyphw/devtool', workflow='devtool-android-unsigned-release.yml',
            artifact='devtool-android-unsigned-release', apk='unsigned-release.apk',
            application_id='io.github.mikeceead.devtoolhud', variant='release', build_type='release',
            target='android_hud', project_dir='android/devtool-live-hud', module='app',
            signing_identity_id='', signer_sha256='', required_files=[], forbidden_files=[])


def load_consumers(root: Path) -> list[dict]:
    path = Path(root) / 'config/android-consumers.json'
    result = [dict(_HUD)]
    if not path.exists():
        return result
    if path.is_symlink() or path.stat().st_size > 128 * 1024:
        raise GitHubActionsError('Android consumer registry is unsafe or exceeds size bound')
    try:
        source = json.loads(path.read_text('utf-8'))
    except (OSError, UnicodeError, ValueError):
        raise GitHubActionsError('Invalid registered Android consumer JSON') from None
    if not isinstance(source, dict) or source.get('schema_version') != 1 or not isinstance(source.get('consumers'), list):
        raise GitHubActionsError('Unsupported Android consumer registry schema')
    if len(source['consumers']) > 48:
        raise GitHubActionsError('Too many registered Android consumers')
    allowed = set(_HUD)
    for item in source['consumers']:
        if not isinstance(item, dict) or not set(item) <= allowed or set(item) != allowed:
            raise GitHubActionsError('Android consumer must declare complete reviewed contract')
        for key, grammar in [('id', _FILE), ('repository', _REPO), ('workflow', _FILE),
                              ('artifact', _FILE), ('apk', _FILE), ('application_id', _ID),
                              ('variant', _VARIANT), ('target', _FILE)]:
            if not isinstance(item[key], str) or not grammar.fullmatch(item[key]):
                raise GitHubActionsError(f'Invalid Android consumer {key}')
        if not item['workflow'].endswith(('.yml', '.yaml')) or not item['apk'].endswith('.apk'):
            raise GitHubActionsError('Consumer workflow/APK extension mismatch')
        if item['build_type'] not in ('release', 'debug') or not isinstance(item['signing_identity_id'], str) or not isinstance(item['signer_sha256'], str):
            raise GitHubActionsError('Invalid consumer signing/build-type policy')
        if item['build_type'] != 'release':
            raise GitHubActionsError('Unsigned release registration requires release build type')
        if not _FILE.fullmatch(item['signing_identity_id']) or not re.fullmatch(r'[0-9a-f]{64}', item['signer_sha256']):
            raise GitHubActionsError('External consumer requires exact LIFE identity and certificate SHA-256')
        for key in ('project_dir', 'module'):
            if not isinstance(item[key], str) or not (item[key] == '.' and key == 'project_dir') and (not _MEMBER.fullmatch(item[key].replace(':', '/')) or any(p in ('..', '.') for p in item[key].replace(':','/').split('/'))):
                raise GitHubActionsError('Unsafe Android consumer project/module path')
        for key in ('required_files', 'forbidden_files'):
            if not isinstance(item[key], list) or len(item[key]) > 32 or any(not isinstance(p,str) or not _MEMBER.fullmatch(p) or any(part in ('.','..') for part in p.split('/')) for p in item[key]):
                raise GitHubActionsError('Unsafe Android consumer archive member rule')
        if set(item['required_files']) & set(item['forbidden_files']):
            raise GitHubActionsError('Contradictory Android archive member rules')
        result.append(dict(item))
    signatures = [(i['repository'].lower(), i['workflow'], i['artifact'], i['variant']) for i in result]
    if len(signatures) != len(set(signatures)) or len({i['id'] for i in result}) != len(result):
        raise GitHubActionsError('Ambiguous duplicate Android consumer contract')
    return result


def select_consumer(root: Path, *, consumer_id: str = '', repository: str = '',
                    workflow: str = '', artifact: str = '', variant: str = '') -> dict:
    selected = load_consumers(root)
    if consumer_id:
        selected = [i for i in selected if i['id'] == consumer_id]
    if repository:
        selected = [i for i in selected if i['repository'].lower() == repository.lower()]
    if workflow:
        selected = [i for i in selected if i['workflow'] == workflow]
    if artifact:
        selected = [i for i in selected if i['artifact'] == artifact]
    if variant:
        selected = [i for i in selected if i['variant'] == variant]
    if len(selected) != 1:
        raise GitHubActionsError('Unsigned lane requires a dedicated registered Android consumer identity; missing or ambiguous')
    return selected[0]


def contract_sha256(consumer: dict) -> str:
    return hashlib.sha256(json.dumps(consumer,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def check_packaging(apk: Path, consumer: dict) -> dict:
    """Member identity is checked before AND after signing; no ZIP extraction."""
    with zipfile.ZipFile(apk) as z:
        names = [item.filename for item in z.infolist() if not item.is_dir()]
        if len(names) != len(set(names)):
            raise GitHubActionsError('Android package has duplicate entries')
        present = set(names)
        missing = set(consumer['required_files']) - present
        forbidden = set(consumer['forbidden_files']) & present
        if missing or forbidden:
            raise GitHubActionsError('APK violates registered required/forbidden packaged-file contract')
        return {'required_verified': sorted(consumer['required_files']),
                'forbidden_verified': sorted(consumer['forbidden_files'])}


def check_native_library_pages(apk: Path, *, page_size: int = 16384) -> dict:
    """Verify packaged native ELF PT_LOAD alignment, independently of zipalign.

    zipalign -P 16 verifies ZIP data offsets; it does NOT repair ELF linker
    segment alignment. Both must be checked before claiming 16 KiB readiness.
    """
    import struct
    checked=[]
    with zipfile.ZipFile(apk) as archive:
        for item in archive.infolist():
            name=item.filename
            if item.is_dir() or not re.fullmatch(r'lib/[^/]+/[^/]+\.so',name):
                continue
            if item.file_size > 128*1024*1024:
                raise GitHubActionsError('Packaged Android native library exceeds inspection bound')
            blob=archive.read(item)
            if len(blob)<64 or blob[:4]!=b'\x7fELF' or blob[5]!=1 or blob[4] not in (1,2):
                raise GitHubActionsError('Packaged native library has invalid ELF header')
            cls=blob[4]
            if cls==2:
                phoff=struct.unpack_from('<Q',blob,32)[0]
                entsize=struct.unpack_from('<H',blob,54)[0]
                count=struct.unpack_from('<H',blob,56)[0]
                align_off=48
            else:
                phoff=struct.unpack_from('<I',blob,28)[0]
                entsize=struct.unpack_from('<H',blob,42)[0]
                count=struct.unpack_from('<H',blob,44)[0]
                align_off=28
            if count<1 or count>512 or entsize<align_off+4 or phoff+entsize*count>len(blob):
                raise GitHubActionsError('Packaged ELF program headers are malformed')
            loads=0
            for index in range(count):
                off=phoff+entsize*index
                kind=struct.unpack_from('<I',blob,off)[0]
                if kind!=1: continue
                loads+=1
                align=(struct.unpack_from('<Q' if cls==2 else '<I',blob,off+align_off)[0])
                if align<page_size or align & (align-1):
                    raise GitHubActionsError('Packaged native ELF PT_LOAD segment is not 16 KiB page aligned')
            if loads==0:
                raise GitHubActionsError('Packaged native library lacks loadable ELF segments')
            checked.append(name)
    return {'native_libraries_verified':sorted(checked),'minimum_page_size':page_size}
