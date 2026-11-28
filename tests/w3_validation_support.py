"""Bounded W3 native validation, with private exact-input module receipts.

The native artifact command owns the result.  This module neither applies source
nor qualifies CI.  Hosted W3 and CI24 remain independent exhaustive gates.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from typing import Iterable

from devtool import host_file_lock
from devtool.xdg import state_dir
from xml.etree import ElementTree as ET

SCHEMA = 'devtool.w3-local-module.v1'
# Audited semantic edges supplement lexical imports/patch targets. Unknown
# production/config changes expand to the entire W3 foundation inventory.
IMPACT = {
    'devtool/delivery_cli.py': ('test_delivery_adoption_cli', 'test_delivery_session', 'test_delivery_cli_surface', 'test_delivery_ci_policy', 'test_delivery_ci_gateway_retry', 'test_delivery_ci_source_recheck', 'test_dt_hotfix_m6'),
    'devtool/m6_live.py': ('test_m6_live', 'test_delivery_rich', 'test_dt_hotfix_m6', 'test_delivery_base_rich_projection'),
    'devtool/ga00_p16_selection.py': ('test_ga00_p16_selection', 'test_ga01_scoped_auth', 'test_github_app_m4', 'test_ga02_live_registry'),
    'devtool/overlay/recovery.py': ('test_overlay_r06d_recovery_plan', 'test_transactions', 'test_next00_next01_historical_retirement', 'test_native_delivery_continuity_m3'),
    'tests/w3_native_validate.py': ('test_w3_native_validation', 'test_ga02_validator_inventory'),
    'tests/w3_validation_support.py': ('test_w3_native_validation',),
    '.github/workflows/devtool-ga-source-acceptance.yml': ('test_w3_native_validation', 'test_ga02_validator_inventory', 'test_qualification_workflows'),
    'docs/DEVTOOL_IMPROVEMENTS.md': ('test_roadmap_delivery_audit',),
    'docs/DEVTOOL_UNIFIED_ROADMAP.md': ('test_roadmap_delivery_audit',),
    'docs/DEVTOOL_W3_FOUNDATIONAL_ACCEPTANCE.md': ('test_complete_feature_guides',),
}
MANDATORY = ('test_native_delivery_contract_v3', 'test_native_delivery_continuity_m3',
             'test_native_runtime_preflight_m2', 'test_artifact_validation_fail_closed_hotfix',
             'test_validation_manifest_skip_contract', 'test_fc02_transaction_closure',
             'test_ga00_security_baseline', 'test_ga01_scoped_auth', 'test_clipboard',
             'test_overlay_r06d_recovery_plan', 'test_w3_native_validation')


def junit_summary(file: Path) -> dict[str, int]:
    """Parse native pytest JUnit locally, without importing hosted CI tools.

    A regular site-packages ``tools`` package can shadow the repository's
    implicit tools namespace, even with the checkout first on sys.path.
    Missing, malformed, empty or unreadable reports cannot establish PASS.
    """
    result = dict(tests=0, failures=0, errors=0, skipped=0)
    try:
        root = ET.parse(file).getroot()
    except (OSError, ET.ParseError):
        return {**result, 'errors': 1}
    if root.tag not in ('testsuite', 'testsuites'):
        return {**result, 'errors': 1}
    for case in root.iter('testcase'):
        result['tests'] += 1
        # Preserve every adverse signal, including contradictory XML.
        if case.find('failure') is not None:
            result['failures'] += 1
        if case.find('error') is not None:
            result['errors'] += 1
        if case.find('skipped') is not None:
            result['skipped'] += 1
    return result


def _json_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _git(root: Path, *args: str) -> bytes:
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_TERMINAL_PROMPT': '0'}
    # The actual native transaction root owns Git discovery, not an inherited
    # unrelated GIT_DIR / alternate index.
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR'):
        env.pop(key, None)
    return subprocess.check_output(['git', '-C', str(root), *args], env=env, timeout=30)


def _names(raw: bytes) -> set[str]:
    names = set(os.fsdecode(x) for x in raw.split(b'\0') if x)
    if any(Path(x).is_absolute() or '..' in Path(x).parts or not x for x in names):
        raise RuntimeError('W3_VALIDATION_SOURCE_PATH_UNSAFE')
    return names


def changed_paths(root: Path) -> set[str]:
    return (_names(_git(root, 'diff', '--no-renames', '--name-only', '-z', 'HEAD', '--')) |
            _names(_git(root, 'ls-files', '--others', '--exclude-standard', '-z')))


def select_modules(root: Path, inventory: Iterable[str], changes: Iterable[str], *, standard=False) -> tuple[list[str], str]:
    inventory = tuple(inventory)
    if not inventory or len(set(inventory)) != len(inventory):
        raise RuntimeError('W3_VALIDATION_INVENTORY_INVALID')
    modules = {p.relative_to(root).as_posix(): p for p in (root / 'tests').rglob('test_*.py')
               if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts}
    by_stem = {}
    for name, path in modules.items():
        by_stem.setdefault(path.stem, []).append(name)
    def resolve(stems):
        found = set()
        for stem in stems:
            matches = by_stem.get(stem, [])
            if len(matches) != 1:
                raise RuntimeError('W3_VALIDATION_MANDATORY_MODULE_MISSING_OR_AMBIGUOUS: ' + stem)
            found.update(matches)
        return found
    if set(inventory) - modules.keys():
        raise RuntimeError('W3_NATIVE_REQUIRED_TEST_MISSING')
    selected = resolve(MANDATORY)
    changes = set(changes)
    # No changed-file evidence must never silently select an empty set.
    broaden = standard or not changes
    lexical = []
    for path in sorted(changes):
        if path in modules:
            selected.add(path)
        elif path in IMPACT:
            selected.update(resolve(IMPACT[path]))
        elif path.startswith('docs/') and path.endswith('.md'):
            selected.update(resolve(('test_complete_feature_guides', 'test_roadmap_delivery_audit')))
        else:
            broaden = True
        if path.startswith('devtool/') and path.endswith('.py'):
            dotted = path[:-3].replace('/', '.')
            # Qualified imports/patch targets, plus explicit from-package imports.
            parent, leaf = dotted.rsplit('.', 1)
            lexical.append(re.compile(r'\b' + re.escape(dotted) + r'\b|\bfrom\s+' + re.escape(parent) + r'\s+import\s+(?:[^\n#]*,\s*)?' + re.escape(leaf) + r'\b'))
    # Discover direct test consumers, including tests outside the historic W3
    # inventory. Explicit edges cover subprocess/dynamic imports and fixtures.
    if lexical:
        for name, path in sorted(modules.items()):
            content = path.read_text(encoding='utf-8')
            if any(rx.search(content) for rx in lexical):
                selected.add(name)
    if broaden:
        selected.update(inventory)
    return sorted(selected), 'standard-expanded' if broaden else 'targeted'


class SourceIdentity:
    """Hash all tracked and nonignored candidate bytes; memoize only in-process.

    Content is fully rehashed at start and final closure. Before/after each
    module, compare HEAD/index/path inventory and inode, ctime, mtime, mode,
    size; rehash changed files. No persisted stat-only trust.
    """
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.memo = {}

    def digest(self, *, fresh=False) -> str:
        root = self.root
        head = _git(root, 'rev-parse', 'HEAD').decode().strip()
        index = hashlib.sha256(_git(root, 'ls-files', '--stage', '-z')).hexdigest()
        files = _names(_git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z'))
        if not files or len(files) > 20000:
            raise RuntimeError('W3_VALIDATION_SOURCE_INVENTORY_INVALID')
        records = []
        for name in sorted(files):
            path = root / name
            if any(p.is_symlink() for p in path.parents if p != root and root in p.parents):
                raise RuntimeError('W3_VALIDATION_SOURCE_PARENT_SYMLINK')
            try:
                info = path.lstat()
            except FileNotFoundError:
                records.append((name, 'missing', '')); continue
            key = (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            prior = self.memo.get(name)
            if not fresh and prior and prior[0] == key:
                digest = prior[1]
            else:
                if stat.S_ISLNK(info.st_mode):
                    digest = hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
                elif stat.S_ISREG(info.st_mode):
                    with path.open('rb') as f:
                        digest = hashlib.file_digest(f, 'sha256').hexdigest()
                else:
                    raise RuntimeError('W3_VALIDATION_SOURCE_NOT_FILE')
                after = path.lstat()
                if key != (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise RuntimeError('W3_VALIDATION_SOURCE_CHANGED_DURING_HASH')
                self.memo[name] = key, digest
            records.append((name, info.st_mode, digest))
        return _json_digest({'head':head,'index':index,'files':records})


def primary_root(root: Path) -> Path:
    root = root.resolve()
    raw = os.environ.get('DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT')
    if not raw:
        return root
    primary = Path(raw).resolve()
    if primary == root:
        return primary
    if Path(os.environ.get('DEVTOOL_TRANSACTION_REPO_ROOT', '')).resolve() != root:
        raise RuntimeError('W3_VALIDATION_EXECUTION_ROOT_DRIFT')
    def common(p):
        value = Path(os.fsdecode(_git(p, 'rev-parse', '--git-common-dir')).strip())
        return (p/value).resolve()
    if common(primary) != common(root) or _git(primary, 'rev-parse','HEAD') != _git(root,'rev-parse','HEAD'):
        raise RuntimeError('W3_VALIDATION_PRIMARY_ROOT_DRIFT')
    return primary


def _private_dir(path: Path) -> None:
    if any(p.is_symlink() for p in (path,path.parent,path.parent.parent)):
        raise RuntimeError('W3_VALIDATION_STATE_UNSAFE')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('W3_VALIDATION_STATE_UNSAFE')


def checkpoint_root(primary: Path) -> Path:
    return state_dir() / 'w3-module-validation' / hashlib.sha256(os.fsencode(str(primary.resolve()))).hexdigest()


@contextmanager
def lease(folder: Path):
    _private_dir(folder.parent)
    _private_dir(folder)
    fd = os.open(folder/'worker.lock', os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0), 0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('W3_VALIDATION_LOCK_UNSAFE')
        try:
            host_file_lock.flock(fd,host_file_lock.LOCK_EX|host_file_lock.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('W3_VALIDATION_WORKER_STILL_ACTIVE') from exc
        # Do not explicitly unlock a shared open-file description: a worker
        # surviving parent death must retain the lease until it exits.
        yield fd
    finally:
        os.close(fd)


def _environment(root: Path, scratch: Path) -> dict[str,str]:
    # Tests run without owner credentials, external pytest selection knobs,
    # transaction identities or mutable Python import paths from the caller.
    keep = ('PATH','LANG','LC_ALL','LC_CTYPE','TERM','PREFIX','ANDROID_ROOT','ANDROID_DATA','ANDROID_RUNTIME_ROOT',
            'LD_LIBRARY_PATH','JAVA_HOME','SHELL','SYSTEMROOT','WINDIR','TZ','SSL_CERT_FILE','SSL_CERT_DIR')
    env = {k:os.environ[k] for k in keep if k in os.environ}
    scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ('home','tmp','state','data','cache','pycache'):
        (scratch/name).mkdir(mode=0o700,exist_ok=True)
    env.update(HOME=str(scratch/'home'),TMPDIR=str(scratch/'tmp'),XDG_STATE_HOME=str(scratch/'state'),
               XDG_DATA_HOME=str(scratch/'data'),XDG_CACHE_HOME=str(scratch/'cache'),
               PYTHONPYCACHEPREFIX=str(scratch.parent/'shared-pycache'),PYTHONPATH=str(root),
               PYTHONUNBUFFERED='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',GIT_CONFIG_NOSYSTEM='1',GIT_TERMINAL_PROMPT='0',
               GIT_AUTHOR_NAME='Devtool validation fixture',GIT_AUTHOR_EMAIL='fixture@invalid.example',
               GIT_COMMITTER_NAME='Devtool validation fixture',GIT_COMMITTER_EMAIL='fixture@invalid.example')
    return env


def runtime_identity(env: dict[str,str]) -> str:
    # Bind to interpreter, installed distribution versions/metadata and tools.
    # Tool content is hashed once per validation invocation, no credentials.
    tools={}
    for name in ('git','git-lfs','rsync','bash','sh','zsh','jq','keytool','java','openssl','gh','curl','tar','zip','unzip','ssh','python3'):
        path=shutil.which(name,path=env.get('PATH'))
        if path:
            with open(path,'rb') as f: tools[name]=(str(Path(path).resolve()),hashlib.file_digest(f,'sha256').hexdigest())
        else: tools[name]=None
    with open(sys.executable,'rb') as f: interpreter=hashlib.file_digest(f,'sha256').hexdigest()
    # Disable unrelated plugin autoload. Bind actual bytes of the relevant
    # runner/tooling distributions, not just a version string or RECORD claim.
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    distributions={canonicalize_name(d.metadata.get('Name','')):d for d in importlib.metadata.distributions()}
    needed={'pytest','pytest-subtests','rich','pyyaml','textual','cryptography','pexpect','packaging','setuptools','wheel','coverage'}
    relevant=set(); pending=list(needed)
    while pending:
        name=canonicalize_name(pending.pop())
        if name in relevant:continue
        relevant.add(name); dist=distributions.get(name)
        if dist:
            for value in dist.requires or ():
                requirement=Requirement(value)
                if requirement.marker is None or requirement.marker.evaluate({'extra':''}):
                    pending.append(requirement.name)
    packages=[]
    for name in sorted(relevant):
        dist=distributions.get(name)
        if dist is None:
            packages.append((name,None));continue
        files=[]
        for item in sorted(dist.files or (),key=str):
            if str(item).endswith(('.pyc','.pyo')):continue
            path=Path(dist.locate_file(item))
            if path.is_file():
                with path.open('rb') as f: value=hashlib.file_digest(f,'sha256').hexdigest()
                files.append((str(item),value))
            else:files.append((str(item),None))
        packages.append((name,dist.version,files))
    # Ignore only our own transient fixture paths; no owner environment values
    # are written into the checkpoint, only the composite digest.
    settings={k:v for k,v in env.items() if k not in ('HOME','TMPDIR','XDG_STATE_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','PYTHONPYCACHEPREFIX','PYTHONPATH')}
    return _json_digest({'python':sys.version,'interpreter':interpreter,'platform':platform.platform(),
                         'packages':packages,'tools':tools,'settings':settings})


def _write(path: Path, body: dict) -> None:
    record={**body,'integrity_sha256':_json_digest(body)}
    fd,name=tempfile.mkstemp(prefix='.module-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(record,f,sort_keys=True,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(name,path)
        fd2=os.open(path.parent,os.O_RDONLY)
        try: os.fsync(fd2)
        finally: os.close(fd2)
    finally:
        if os.path.exists(name):os.unlink(name)


def _read_pass(path: Path, key: str, module: str) -> dict|None:
    if not path.exists() and not path.is_symlink(): return None
    try:
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        with os.fdopen(fd,'rb') as f:
            st=os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode&0o077 or st.st_size>16384:
                return None
            row=json.loads(f.read(16385))
        checksum=row.pop('integrity_sha256',None)
        if checksum!=_json_digest(row) or row.get('schema')!=SCHEMA or row.get('key')!=key or row.get('module')!=module or row.get('status')!='PASS' or row.get('exit_code')!=0:
            return None
        counts=row['counts']
        if not all(type(counts.get(k)) is int and counts[k]>=0 for k in ('tests','failures','errors','skipped')) or counts['tests']<=counts['skipped'] or counts['failures'] or counts['errors']:
            return None
        return row
    except (OSError,ValueError,KeyError,TypeError,AttributeError):return None


def _run_module(root:Path, name:str, scratch:Path, fd:int, timeout:int=600) -> tuple[int,dict,float]:
    xml=scratch/'result.xml'
    env=_environment(root,scratch)
    argv=[sys.executable,'-m','pytest','-q','-p','no:cacheprovider','--devtool-lane','full',f'--junitxml={xml}']
    # pytest >=9 has native unittest subtest support. Older supported pytest
    # needs the explicit plugin when available; no unrelated plugins autoload.
    if int(importlib.metadata.version('pytest').split('.',1)[0]) < 9:
        try: importlib.metadata.version('pytest-subtests')
        except importlib.metadata.PackageNotFoundError: raise RuntimeError('W3_PYTEST_SUBTESTS_REQUIRED') from None
        argv.extend(('-p','pytest_subtests.plugin'))
    argv.append(name)
    start=time.monotonic()
    # Native console owns live streams. Inherit lease into the child so a
    # force-killed parent cannot falsely allow concurrent resume.
    proc=subprocess.Popen(argv,cwd=root,env=env,pass_fds=(fd,))
    try:
        rc=proc.wait(timeout=timeout)
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
        raise
    return rc,junit_summary(xml),round(time.monotonic()-start,3)


def reviewed_native_plan(root: Path, inventory: Iterable[str], changes: Iterable[str],
                         reviewed_changes: Iterable[str], modules: Iterable[str]) -> list[str]:
    """Finite, explicitly reviewed owner-side gate; comprehensive CI24 stays pending."""
    expected, actual = tuple(reviewed_changes), set(changes)
    selected, full = tuple(modules), tuple(inventory)
    if (not expected or len(set(expected)) != len(expected) or not actual
            or actual != set(expected)):
        raise RuntimeError('NATIVE_CI24_REVIEWED_CHANGE_SET_MISMATCH')
    if (not selected or len(set(selected)) != len(selected) or len(selected) > 24
            or len(set(full)) != len(full) or not full or not set(selected) < set(full)
            or not set(MANDATORY) <= {Path(name).stem for name in selected}):
        raise RuntimeError('NATIVE_CI24_BOUNDED_INVENTORY_INVALID')
    for name in selected:
        rel = Path(name)
        path = root / rel
        if (rel.is_absolute() or '..' in rel.parts or len(rel.parts) < 2
                or rel.parts[0] != 'tests' or not rel.name.startswith('test_')
                or rel.suffix != '.py' or not path.is_file()
                or any(parent.is_symlink() for parent in (path, *path.parents) if parent == root or root in parent.parents)):
            raise RuntimeError('NATIVE_CI24_REQUIRED_MODULE_MISSING_OR_UNSAFE: ' + name)
    return sorted(selected)


def run_local(root:Path, inventory:Iterable[str], *, standard=False,
              reviewed_changes:Iterable[str]|None=None,
              reviewed_native_modules:Iterable[str]|None=None) -> int:
    primary=primary_root(root)
    folder=checkpoint_root(primary)
    # The state directory is durable. All execution/JUnit scratch is private
    # under the real tempfile/TMPDIR root, never shared storage.
    with lease(folder) as fd, tempfile.TemporaryDirectory(prefix='w3-validation-') as tmp:
        scratch=Path(tmp)
        source=SourceIdentity(root)
        source_key=source.digest(fresh=True)
        changes=changed_paths(root)
        # A separately reviewed exact package may declare its complete local
        # regression set. This avoids treating a small CLI routing edit as
        # touching every CLI consumer on the phone. Unknown/extra changes
        # retain the original dependency expansion; W3 callers are unchanged.
        if reviewed_native_modules is not None:
            if standard or reviewed_changes is None:
                raise RuntimeError('NATIVE_CI24_REVIEWED_PROFILE_CONFLICT')
            selected = reviewed_native_plan(root, inventory, changes, reviewed_changes, reviewed_native_modules)
            profile = 'reviewed-native-ci24'
        elif reviewed_changes is not None and changes and changes==set(reviewed_changes):
            selected,_=select_modules(root,inventory,(),standard=True)
            profile='reviewed-exact-package'
        else:
            selected,profile=select_modules(root,inventory,changes,standard=standard)
        runtime=runtime_identity(_environment(root,scratch/'identity'))
        campaign=_json_digest({'schema':SCHEMA,'primary':str(primary),'source':source_key,'runtime':runtime,'profile':profile,'modules':selected})
        dest=folder/campaign;_private_dir(dest)
        print(f'W3_LOCAL_PLAN profile={profile} modules={len(selected)} full_w3_inventory={len(tuple(inventory))} source={source_key} campaign={campaign} hosted_ci24_required=true',flush=True)
        total=executed=reused=skipped=0
        for i,name in enumerate(selected,1):
            if source.digest()!=source_key:raise RuntimeError('W3_VALIDATION_SOURCE_DRIFT')
            file=dest/(hashlib.sha256(name.encode()).hexdigest()+'.json')
            old=_read_pass(file,campaign,name)
            if old:
                reused+=1;row=old
                print(f'W3_LOCAL_MODULE {i}/{len(selected)} REUSED {name} tests={row["counts"]["tests"]}',flush=True)
            else:
                print(f'W3_LOCAL_MODULE {i}/{len(selected)} RUN {name}',flush=True)
                unit=scratch/f'module-{i}';unit.mkdir(mode=0o700)
                rc,counts,seconds=_run_module(root,name,unit,fd)
                if source.digest()!=source_key:raise RuntimeError('W3_VALIDATION_SOURCE_DRIFT')
                if rc!=0 or counts['tests']<=counts['skipped'] or counts['failures'] or counts['errors']:
                    print(f'W3_LOCAL_MODULE FAIL {name} exit={rc} counts={counts}',flush=True)
                    return rc if rc>0 else 128-rc if rc<0 else 1
                row={'schema':SCHEMA,'key':campaign,'module':name,'status':'PASS','exit_code':0,'counts':counts,'seconds':seconds}
                _write(file,row)
                if _read_pass(file,campaign,name)!=row:raise RuntimeError('W3_VALIDATION_CHECKPOINT_READBACK_FAILED')
                executed+=1
                print(f'W3_LOCAL_MODULE {i}/{len(selected)} PASS {name} seconds={seconds}',flush=True)
            total+=row['counts']['tests'];skipped+=row['counts']['skipped']
        if source.digest(fresh=True)!=source_key or runtime_identity(_environment(root,scratch/'identity'))!=runtime:
            raise RuntimeError('W3_VALIDATION_SOURCE_OR_RUNTIME_DRIFT')
        print(f'W3_LOCAL_COMPLETE profile={profile} modules={len(selected)} executed={executed} reused={reused} tests={total} skipped={skipped} hosted_ci24_required=true',flush=True)
        return 0
