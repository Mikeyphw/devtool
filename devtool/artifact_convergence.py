"""RM-05: read-only producer-bound artifacts and deterministic test shards.

Content integrity never implies authenticated origin, signature, hosted authority,
consumer acceptance, device qualification or release qualification.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
from email.parser import Parser
import re
import stat
import tarfile
import zipfile
from typing import Any, Mapping
import xml.etree.ElementTree as ET

SCHEMA = 2
MAX_FILE = 512 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024
MAX_MEMBER = 256 * 1024 * 1024
MAX_MEMBERS = 20000
MAX_REPORT = 16 * 1024 * 1024
MAX_SHARDS = 256
SHA40 = re.compile(r'[a-f0-9]{40}\Z')
SHA64 = re.compile(r'[a-f0-9]{64}\Z')
IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,255}\Z')

class ConvergenceError(ValueError):
    pass


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise ConvergenceError(f'{field}: invalid identity')
    return value


def _sha(value: Any, field: str, size: int = 64) -> str:
    if not isinstance(value, str) or not (SHA64 if size == 64 else SHA40).fullmatch(value):
        raise ConvergenceError(f'{field}: invalid SHA-{size * 4}')
    return value


def _bounded_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_REPORT:
        raise ConvergenceError('receipt missing, symlinked, empty or oversized')
    try:
        value = json.loads(path.read_text('utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ConvergenceError('malformed receipt: ' + type(exc).__name__) from None
    if not isinstance(value, dict):
        raise ConvergenceError('receipt must be an object')
    return value


def _relative(root: Path, value: Any, *, size: int = MAX_FILE) -> Path:
    if not isinstance(value, str) or not 0 < len(value) <= 512 or '\\' in value:
        raise ConvergenceError('unsafe repository-relative path')
    parts = PurePosixPath(value).parts
    if value.startswith('/') or any(v in ('', '.', '..') for v in parts) or ':' in parts[0]:
        raise ConvergenceError('unsafe repository-relative path')
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ConvergenceError('symlink in artifact path')
    if not path.is_file() or not 0 < path.stat().st_size <= size:
        raise ConvergenceError('missing, empty or oversized artifact/report')
    return path


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _member(name: str) -> None:
    if not name or '\\' in name or name.startswith('/') or '\x00' in name:
        raise ConvergenceError('unsafe archive member')
    parts = name.rstrip('/').split('/')
    if any(p in ('', '.', '..') for p in parts) or ':' in parts[0]:
        raise ConvergenceError('archive traversal or invalid member')


def _archive(path: Path, kind: str) -> dict[str, Any]:
    members: set[str] = set()
    expanded = 0
    if kind in {'zip', 'apk', 'aab', 'jar', 'wheel', 'nupkg', 'web-bundle'}:
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                if not infos or len(infos) > MAX_MEMBERS:
                    raise ConvergenceError('empty/oversized archive inventory')
                for info in infos:
                    _member(info.filename)
                    if info.filename in members:
                        raise ConvergenceError('duplicate archive member')
                    members.add(info.filename)
                    mode = info.external_attr >> 16
                    if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                        raise ConvergenceError('archive link/special file')
                    if info.flag_bits & 1:
                        raise ConvergenceError('encrypted archive member')
                    if info.file_size > MAX_MEMBER or (info.file_size > 1024 * 1024 and
                            info.file_size > max(info.compress_size, 1) * 1000):
                        raise ConvergenceError('zip bomb or oversized member')
                    expanded += info.file_size
                    if expanded > MAX_EXPANDED:
                        raise ConvergenceError('archive expanded size over limit')
                if archive.testzip() is not None:
                    raise ConvergenceError('archive CRC mismatch')
        except (zipfile.BadZipFile, OSError, EOFError, RuntimeError) as exc:
            raise ConvergenceError('invalid ZIP container: ' + type(exc).__name__) from None
        expected = {'apk': 'AndroidManifest.xml', 'aab': 'BundleConfig.pb',
                    'jar': 'META-INF/MANIFEST.MF'}
        if kind in expected and expected[kind] not in members:
            raise ConvergenceError('required package member missing')
        if kind == 'wheel':
            if not path.name.endswith('.whl') or len([n for n in members if n.endswith('.dist-info/METADATA')]) != 1:
                raise ConvergenceError('wheel identity/metadata unavailable')
        if kind == 'nupkg' and not any(n.endswith('.nuspec') for n in members):
            raise ConvergenceError('NuGet package metadata missing')
        if kind == 'web-bundle' and not ('index.html' in members or 'package.json' in members):
            raise ConvergenceError('web bundle entry point missing')
    elif kind in {'tar', 'sdist', 'archive'}:
        try:
            with tarfile.open(path, 'r:*') as archive:
                infos = archive.getmembers()
                if not infos or len(infos) > MAX_MEMBERS:
                    raise ConvergenceError('empty/oversized archive inventory')
                for item in infos:
                    _member(item.name)
                    if item.name in members:
                        raise ConvergenceError('duplicate archive member')
                    members.add(item.name)
                    if not (item.isfile() or item.isdir()):
                        raise ConvergenceError('archive links/special files forbidden')
                    if item.size > MAX_MEMBER:
                        raise ConvergenceError('oversized archive member')
                    expanded += item.size
                    if expanded > MAX_EXPANDED:
                        raise ConvergenceError('archive expanded size over limit')
                if kind == 'sdist' and not any(n.endswith('/PKG-INFO') for n in members):
                    raise ConvergenceError('sdist PKG-INFO missing')
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise ConvergenceError('invalid TAR container: ' + type(exc).__name__) from None
    else:
        raise ConvergenceError('unknown archive kind')
    return {'members': len(members), 'expanded_bytes': expanded, 'archive_structure': 'PASS'}


VERIFIER_KINDS = frozenset({'zip', 'apk', 'aab', 'jar', 'wheel', 'nupkg',
    'web-bundle', 'tar', 'sdist', 'archive', 'go-binary', 'native-binary'})


def _package_identity(path: Path, kind: str, expected: str | None) -> bool:
    """Never accept an unparsed external identity just because bytes hash."""
    if expected is None:
        return False
    try:
        if kind == 'wheel':
            from .actions_distributions import verify_wheel
            name, sep, version = expected.partition('@')
            if not sep or not name or not version:
                raise ConvergenceError('wheel package_id requires name@version')
            verify_wheel(path, name=name, version=version, entrypoints=[])
            return True
        if kind in {'nupkg','web-bundle','jar','sdist'}:
            if kind == 'sdist':
                with tarfile.open(path,'r:*') as t:
                    entries=[m for m in t.getmembers() if m.name.endswith('/PKG-INFO')]
                    if len(entries)!=1:raise ConvergenceError('sdist package identity ambiguous')
                    stream=t.extractfile(entries[0]); assert stream is not None
                    data=stream.read(1024*1024).decode('utf-8')
                attrs=Parser().parsestr(data)
                value=attrs.get('Name','')+'@'+attrs.get('Version','')
            else:
                with zipfile.ZipFile(path) as z:
                    if kind=='nupkg':
                        entries=[n for n in z.namelist() if n.endswith('.nuspec')]
                        if len(entries)!=1:raise ConvergenceError('NuGet identity ambiguous')
                        xml=z.read(entries[0]); root=ET.fromstring(xml)
                        fields={e.tag.rsplit('}',1)[-1]:e.text for e in root.iter()}
                        value=str(fields.get('id') or '')+'@'+str(fields.get('version') or '')
                    elif kind=='web-bundle':
                        if 'package.json' not in z.namelist():
                            raise ConvergenceError('package.json missing for package identity')
                        obj=json.loads(z.read('package.json'))
                        value=str(obj.get('name') or '')+'@'+str(obj.get('version') or '')
                    else:
                        manifest=z.read('META-INF/MANIFEST.MF').decode('utf-8')
                        attrs=Parser().parsestr(manifest.replace('\r\n','\n'))
                        value=str(attrs.get('Implementation-Title') or '')+'@'+str(attrs.get('Implementation-Version') or '')
            if value != expected:
                raise ConvergenceError('declared package identity mismatch')
            return True
        if kind in {'apk','aab'}:
            # Android binary manifests require provider-owned aapt/aapt2 parsing.
            # This portable offline verifier intentionally does not guess IDs.
            raise ConvergenceError('compiled Android package identity needs provider-owned manifest verification')
        if kind in {'go-binary','native-binary'}:
            raise ConvergenceError('binary module identity requires an explicit toolchain/buildinfo verifier')
        raise ConvergenceError('artifact format does not expose a trusted package identity')
    except (UnicodeError, KeyError, ValueError, ET.ParseError, json.JSONDecodeError) as exc:
        if isinstance(exc, ConvergenceError):raise
        raise ConvergenceError('package identity unreadable: '+type(exc).__name__) from None


def _format_verify(path: Path, kind: str) -> dict[str, Any]:
    if kind in {'zip', 'apk', 'aab', 'jar', 'wheel', 'nupkg', 'web-bundle', 'tar', 'sdist', 'archive'}:
        result = _archive(path, kind)
        if kind == 'wheel':
            from .actions_distributions import verify_wheel
            parts=path.name[:-4].split('-')
            if len(parts) not in (5,6):
                raise ConvergenceError('wheel filename lacks identity/tags')
            try:
                verdict=verify_wheel(path,name=parts[0],version=parts[1],entrypoints=[])
            except (ValueError, OSError) as exc:
                raise ConvergenceError('wheel member RECORD verification failed: '+str(exc)) from None
            result['wheel_record_sha256_verified'] = verdict['record_sha256_verified']
        return result
    if kind in {'go-binary', 'native-binary'}:
        with path.open('rb') as f:
            head = f.read(8)
        if not (head.startswith(b'\x7fELF') or head.startswith(b'MZ') or
                head[:4] in (b'\xfe\xed\xfa\xce',b'\xce\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xcf\xfa\xed\xfe')):
            raise ConvergenceError('unsupported binary file header')
        return {'binary_header': 'PASS', 'go_build_metadata': 'UNKNOWN' if kind == 'go-binary' else None}
    raise ConvergenceError('unregistered verifier kind: ' + str(kind))


def _identity(value: Any, *, name: str) -> dict:
    if not isinstance(value, dict) or set(value) != {'node', 'source_sha', 'run_id', 'attempt'}:
        raise ConvergenceError(name + ': identity fields must be exact')
    out = {'node': _id(value['node'], name+'.node'),
           'source_sha': _sha(value['source_sha'], name+'.source_sha', 40),
           'run_id': _id(value['run_id'], name+'.run_id')}
    attempt = value['attempt']
    if type(attempt) is not int or not 1 <= attempt <= 999999:
        raise ConvergenceError(name + ': attempt must be positive integer')
    return {**out, 'attempt':attempt}


def _expected_match(producer: dict, expected: Mapping[str, Any] | None) -> None:
    for k, value in (expected or {}).items():
        if k not in producer or producer[k] != value:
            raise ConvergenceError('expected producer identity mismatch: '+k)


def _attestation(root: Path, attestation: Any, artifact_sha: str, producer: dict,
                 trusted_keys: Mapping[str, bytes] | None) -> dict:
    if attestation is None:
        return {'state':'UNSIGNED', 'authenticated':False, 'reason':'no signature/attestation supplied'}
    if not isinstance(attestation, dict) or set(attestation) != {'payload', 'signature', 'key_id'}:
        raise ConvergenceError('attestation must specify payload, signature and key_id')
    key_id = _id(attestation['key_id'], 'attestation.key_id')
    try:
        payload = base64.b64decode(attestation['payload'], validate=True)
        sig = base64.b64decode(attestation['signature'], validate=True)
        body = json.loads(payload)
    except (ValueError, UnicodeError, TypeError):
        raise ConvergenceError('attestation encoding invalid') from None
    if not isinstance(body, dict) or body != {'artifact_sha256':artifact_sha, 'producer':producer}:
        raise ConvergenceError('attestation predicate does not bind exact producer/artifact')
    if trusted_keys is None or key_id not in trusted_keys:
        return {'state':'SIGNED_UNVERIFIED', 'authenticated':False, 'reason':'no pinned trusted public key'}
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = Ed25519PublicKey.from_public_bytes(trusted_keys[key_id])
        key.verify(sig, payload)
    except ImportError:
        raise ConvergenceError('Ed25519 verification unavailable; cannot claim verified signature') from None
    except Exception as exc:
        raise ConvergenceError('attestation signature verification failed: '+type(exc).__name__) from None
    return {'state':'VERIFIED', 'authenticated':True, 'key_id':key_id,
            'scope':'pinned_public_key_only_not_release_qualification'}


def verify_artifact_receipt(root: Path, receipt_file: Path, *, expected: Mapping[str, Any] | None = None,
                            trusted_keys: Mapping[str, bytes] | None = None) -> dict:
    root = Path(root).resolve()
    r = _bounded_json(Path(receipt_file))
    if set(r) != {'schema_version','kind','producer','artifact','consumers','attestation','sbom'} or \
            r['schema_version'] != SCHEMA or r['kind'] != 'devtool.artifact-receipt':
        raise ConvergenceError('unsupported/ambiguous ArtifactReceiptV2')
    producer = _identity(r['producer'], name='producer')
    _expected_match(producer, expected)
    art = r['artifact']
    if not isinstance(art, dict) or set(art) != {'kind','path','sha256','package_id'}:
        raise ConvergenceError('artifact declaration is incomplete')
    kind = _id(art['kind'], 'artifact.kind')
    expected_sha = _sha(art['sha256'], 'artifact.sha256')
    path = _relative(root, art['path'])
    if _digest(path) != expected_sha:
        raise ConvergenceError('artifact digest mismatch')
    pkg_id = art['package_id']
    if pkg_id is not None:
        _id(pkg_id, 'artifact.package_id')
    content = _format_verify(path, kind)
    content["package_identity_verified"] = _package_identity(path, kind, pkg_id)
    consumers = r['consumers']
    if not isinstance(consumers, list) or len(consumers) > 1024:
        raise ConvergenceError('invalid bounded consumer declaration')
    nodes = set()
    for c in consumers:
        if not isinstance(c, dict) or set(c) != {'node','sha256'} or c['node'] in nodes:
            raise ConvergenceError('duplicate/malformed consumer')
        node = _id(c['node'], 'consumer.node')
        nodes.add(node)
        if node == producer['node'] or _sha(c['sha256'], 'consumer.sha256') != expected_sha:
            raise ConvergenceError('consumer dependency does not bind producer digest')
    sbom = r['sbom']
    sbom_state = 'ABSENT'
    if sbom is not None:
        if not isinstance(sbom, dict) or set(sbom) != {'path','sha256'}:
            raise ConvergenceError('invalid SBOM reference')
        sbom_path = _relative(root, sbom['path'], size=MAX_REPORT)
        if _digest(sbom_path) != _sha(sbom['sha256'], 'sbom.sha256'):
            raise ConvergenceError('SBOM digest mismatch')
        sbom_state = 'INTEGRITY_VERIFIED_NOT_ATTESTED'
    signature = _attestation(root, r['attestation'], expected_sha, producer, trusted_keys)
    return {'schema_version':SCHEMA, 'kind':'devtool.artifact-verification', 'integrity':'PASS',
            'artifact':{'kind':kind,'path':art['path'],'sha256':expected_sha,'package_id':pkg_id, **content},
            'producer': producer, 'consumers': sorted(nodes), 'signature': signature,
            'sbom':{'state':sbom_state}, 'source_identity_matched_to_caller':bool(expected), 'consumer_binding':'DECLARED_HASH_BOUND_NOT_EXECUTED', 'read_only':True,
            'remote_authenticated':False, 'device_qualified':False, 'release_qualified':False}


def _test_cases(path: Path, fmt: str) -> dict[str,str]:
    out: dict[str,str] = {}
    def add(name: str, outcome: str):
        if not isinstance(name,str) or not 1 <= len(name) <= 512 or any(ord(c) < 32 for c in name):
            raise ConvergenceError('invalid test identity')
        if name in out:
            raise ConvergenceError('duplicate terminal test identity: '+name)
        out[name] = outcome
        if len(out) > 250000:
            raise ConvergenceError('too many test cases')
    if fmt in {'junit','pytest-junit','js-junit','trx'}:
        try:
            data = path.read_bytes()
            if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                raise ConvergenceError('XML entities forbidden')
            root = ET.fromstring(data)
        except ET.ParseError:
            raise ConvergenceError('truncated/malformed XML report') from None
        tag = lambda e: e.tag.rsplit('}',1)[-1]
        if fmt == 'trx':
            if tag(root) != 'TestRun': raise ConvergenceError('expected TRX root')
            for el in root.iter():
                if tag(el) != 'UnitTestResult': continue
                name = (el.get('testId') or '') + ':' + (el.get('executionId') or '')
                outcome = el.get('outcome')
                if outcome not in {'Passed','Failed','Error','Timeout','Aborted','NotExecuted','Inconclusive','NotRunnable','Pending'}:
                    raise ConvergenceError('unknown TRX outcome')
                add(name,'passed' if outcome=='Passed' else ('skipped' if outcome in {'NotExecuted','Inconclusive','NotRunnable','Pending'} else 'failed'))
        else:
            if tag(root) not in {'testsuites','testsuite'}: raise ConvergenceError('expected JUnit root')
            for el in root.iter():
                if tag(el) != 'testcase': continue
                name = (el.get('classname') or 'suite') + ':' + (el.get('name') or '')
                child = {tag(item) for item in el}
                add(name, 'failed' if child & {'error','failure'} else ('skipped' if 'skipped' in child else 'passed'))
    elif fmt in {'go-json','cargo-json'}:
        started: set[str] = set()
        for line in path.read_text('utf-8').splitlines():
            if not line.strip(): continue
            try: event = json.loads(line)
            except ValueError: raise ConvergenceError('truncated/malformed JSON event') from None
            if not isinstance(event, dict): raise ConvergenceError('invalid JSON test event')
            if fmt == 'go-json':
                action = event.get('Action'); test = event.get('Test'); package = event.get('Package')
                if not isinstance(action,str) or action not in {'start','run','pause','cont','output','pass','fail','skip'}:
                    raise ConvergenceError('unknown Go event action')
                if action == 'fail' and not test: raise ConvergenceError('Go package failure without test identity')
                if not test: continue
                name = str(package or 'package')+':'+str(test)
                if action=='run': started.add(name)
                if action in {'pass','fail','skip'}:
                    add(name, {'pass':'passed','fail':'failed','skip':'skipped'}[action]); started.discard(name)
            else:
                if event.get('type') != 'test': continue
                name = str(event.get('name') or '')
                action = event.get('event')
                if action == 'started': started.add(name)
                elif action in {'ok','failed','ignored'}:
                    add(name, {'ok':'passed','failed':'failed','ignored':'skipped'}[action]); started.discard(name)
                elif action != 'timeout': raise ConvergenceError('unknown Cargo test event')
        if started: raise ConvergenceError('truncated report: started tests lack terminal events')
    else:
        raise ConvergenceError('unsupported report format')
    if not out: raise ConvergenceError('report has no terminal test cases')
    return out


def merge_test_shards(root: Path, manifest_file: Path, *, expected: Mapping[str, Any] | None = None) -> dict:
    root = Path(root).resolve()
    r = _bounded_json(Path(manifest_file))
    if set(r) not in ({'schema_version','kind','producer','shards'}, {'schema_version','kind','producer','shards','exo'}) or r['schema_version'] != SCHEMA or \
            r['kind'] != 'devtool.test-shard-manifest':
        raise ConvergenceError('unsupported/ambiguous test-shard manifest')
    producer = _identity(r['producer'], name='producer');_expected_match(producer, expected)
    shards = r['shards']
    if not isinstance(shards, list) or not 0 < len(shards) <= MAX_SHARDS:
        raise ConvergenceError('invalid test shard count')
    identities: dict[str, tuple[str,str]] = {}
    rows=[]
    for shard in shards:
        if not isinstance(shard, dict) or set(shard) != {'id','node','format','path','sha256'}:
            raise ConvergenceError('invalid test shard declaration')
        sid=_id(shard['id'],'shard.id');node=_id(shard['node'],'shard.node')
        if sid in {row['id'] for row in rows}: raise ConvergenceError('duplicate shard ID')
        path=_relative(root,shard['path'],size=MAX_REPORT)
        sha=_sha(shard['sha256'],'shard.sha256')
        if _digest(path)!=sha: raise ConvergenceError('test shard digest mismatch')
        cases=_test_cases(path,shard['format'])
        for case, outcome in cases.items():
            if case in identities:
                raise ConvergenceError('duplicate case across shards: '+case)
            identities[case]=(outcome,node)
        rows.append({'id':sid,'node':node,'format':shard['format'],'sha256':sha,
                     'total':len(cases),'failed':sum(x=='failed' for x in cases.values()),
                     'skipped':sum(x=='skipped' for x in cases.values())})
    exo = r.get('exo')
    exo_state = 'UNKNOWN'
    if exo is not None:
        if not isinstance(exo, dict) or set(exo) != {'path','sha256'}:
            raise ConvergenceError('invalid EXO journal declaration')
        journal = _relative(root, exo['path'], size=MAX_REPORT)
        if _digest(journal) != _sha(exo['sha256'], 'exo.sha256'):
            raise ConvergenceError('EXO journal digest mismatch')
        terminal: dict[str,str] = {}
        for line in journal.read_text('utf-8').splitlines():
            if not line.strip(): continue
            try: ev = json.loads(line)
            except ValueError: raise ConvergenceError('truncated EXO journal') from None
            if not isinstance(ev, dict) or ev.get('event_type') != 'node.finished':continue
            if ev.get('run_id') != producer['run_id']:
                continue
            node = ev.get('node_id')
            if not isinstance(node, str) or node in terminal:
                raise ConvergenceError('EXO terminal node missing or duplicated')
            terminal[node] = str(ev.get('status') or '')
        for row in rows:
            node = row['node']
            if node not in terminal:
                raise ConvergenceError('EXO terminal for test node missing')
            status = terminal[node]
            if status not in {'passed','succeeded','failed'}:
                raise ConvergenceError('EXO test node did not reach a terminal execution result')
            if (row['failed'] and status != 'failed') or (not row['failed'] and status == 'failed'):
                raise ConvergenceError('EXO node result contradicts report shard')
        exo_state = 'LOCAL_JOURNAL_HASH_CORRELATED_NOT_AUTHENTICATED'
    counts={'total':len(identities),'failed':sum(x[0]=='failed' for x in identities.values()),
            'skipped':sum(x[0]=='skipped' for x in identities.values())}
    return {'schema_version':SCHEMA,'kind':'devtool.test-shard-merge','producer':producer,
            'shards':sorted(rows,key=lambda r:r['id']), 'counts':{**counts,
            'passed':counts['total']-counts['failed']-counts['skipped']},
            'cases':[{'identity':name,'outcome':o,'node':n} for name,(o,n) in sorted(identities.items())],
            'state':'FAIL' if counts['failed'] else 'PASS', 'exo_correlation':{'state':exo_state}, 'read_only':True,
            'trusted_producer_verified':False,'remote_authenticated':False,'release_qualified':False}
