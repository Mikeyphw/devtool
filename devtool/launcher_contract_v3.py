"""Devtool Launcher Contract v3: source-bound, thin, restart-safe Python/Rich zipapps.

This module deliberately does not implement an overlay engine, a GitHub API
client, a scheduler, or a release authority. Devtool owns those operations.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
from typing import Any

SCHEMA = 'devtool.launcher-contract.v3'
SHA40 = re.compile(r'[0-9a-f]{40}\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
SESSION32 = re.compile(r'[0-9a-f]{32}\Z')
GHREPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
MAX_FILES = 48
MAX_TOTAL = 6 * 1024 * 1024
MAX_ONE = 2 * 1024 * 1024

try:
    from rich.console import Console as _RichConsole
    from rich.panel import Panel as _RichPanel
    from rich.text import Text as _RichText
    _RICH = _RichConsole()
except ImportError:
    _RICH = None
    _RichPanel = None
    _RichText = None


def banner(command: str, *, builder: bool = False) -> None:
    role = 'BUILDER' if builder else 'NATIVE DELIVERY'
    message = (f'Devtool Launcher v3 | {role} | {command}\n'
               'Source/tree/CLI bound; Devtool owns validation and sessions\n'
               'No auto merge, force push, local Gradle, device writes or release claims')
    if _RICH is not None and _RichPanel is not None and 'NO_COLOR' not in os.environ and os.environ.get('CLICOLOR') != '0' and os.environ.get('TERM') != 'dumb':
        _RICH.print(_RichPanel(message, title='Devtool Python Launcher Contract', expand=False))
    else:
        say(message)
CLI_CONTRACT = (
    ('delivery', 'adoption', 'audit'),
    ('delivery', 'adoption', 'scaffold'),
    ('validate', 'overlay'),
    ('apply', 'overlay'),
    ('delivery', 'session', 'start'),
    ('delivery', 'session', 'status'),
    ('delivery', 'session', 'push'),
    ('delivery', 'session', 'pr'),
    ('delivery', 'session', 'ci'),
    ('delivery', 'session', 'report'),
    ('delivery', 'session', 'resume'),
    ('overlay', 'recovery-plan'),
    ('artifacts', 'verify'),
    ('capabilities',),
    ('plan',),
)


class Refused(RuntimeError):
    """Fail-closed guard with actionable stage-specific message."""


def say(*args: Any) -> None:
    # Rich is presentation only: unmodified native output still owns the record.
    msg=' '.join(str(item) for item in args)
    if (_RICH is not None and _RichText is not None and _RICH.is_terminal
            and 'NO_COLOR' not in os.environ and os.environ.get('CLICOLOR') != '0'
            and os.environ.get('TERM') != 'dumb'):
        style=('bold red' if 'REFUSED' in msg or 'FAILED' in msg else
               'bold green' if any(x in msg for x in ('_PASS', '_READY', '_DELIVERED', '_COMMITTED', '_VALIDATED')) else
               'cyan' if any(x in msg for x in ('LAUNCHER_NATIVE_BEGIN', '_AUTO_FETCH', '_SESSION_ID')) else 'dim')
        _RICH.print(_RichText(msg, style=style))
    else:
        print(msg, flush=True)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_sha(value: str, label: str) -> str:
    if not isinstance(value, str) or not SHA40.fullmatch(value):
        raise Refused(f'INVALID_FULL_40_CHARACTER_SHA label={label} actual={value!r}')
    return value


def safe_path(path: str) -> str:
    if (not isinstance(path, str) or not path or len(path) > 220 or
            '\\' in path or '\x00' in path):
        raise Refused('INVALID_PAYLOAD_PATH')
    pure = PurePosixPath(path)
    if (pure.is_absolute() or pure.as_posix() != path or
            any(p in ('', '.', '..', '.git') for p in path.split('/')) or
            path in ('__main__.py', 'manifest.json')):
        raise Refused(f'UNSAFE_PAYLOAD_PATH {path!r}')
    return path


def canonical_remote(remote: str) -> str:
    pat = r'(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?\Z'
    m = re.fullmatch(pat, remote, re.I)
    if not m or not GHREPO.fullmatch(m.group(1)):
        raise Refused('NONCANONICAL_GITHUB_ORIGIN')
    return m.group(1)


def safe_env(*, devtool_root: Path | None = None, target_root: Path | None = None, color_output: bool = False) -> dict[str, str]:
    env = dict(os.environ)
    env['GIT_NO_REPLACE_OBJECTS'] = '1'
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    # Native Devtool presentation selectors. Do not emulate or replace its UI.
    env.update({
        'DEVTOOL_EXECUTION_PRESENTATION': 'plain',
        'DEVTOOL_JOB_UI': 'plain',
        'DEVTOOL_TEXTUAL_APPLY_HOST': '0',
        'DEVTOOL_TEXTUAL_EXECUTION_HOST': '0',
    })
    # Keep an attached terminal colorful; never override explicit no-color or
    # manufacture ANSI escape codes in machine/redirected streams.
    if color_output and (sys.stdout.isatty() and env.get('TERM', '') != 'dumb'
            and 'NO_COLOR' not in env and env.get('CLICOLOR') != '0'):
        env.setdefault('FORCE_COLOR', '1')
    elif not color_output:
        env.pop('FORCE_COLOR', None)
        env.setdefault('NO_COLOR', '1')
    if devtool_root is not None:
        # Important: --repo chooses the *target*, not the Python package.
        # Drop inherited PYTHONPATH so a stale installed checkout can't win.
        env['PYTHONPATH'] = str(devtool_root)
        # Devtool's RUN-V3 catalog may otherwise create untracked .devtool/
        # inside the target and invalidate native exact-tree validation.
        # Its documented external XDG store is the native isolation boundary.
        existing = os.environ.get('DEVTOOL_XDG_DIR', '').strip()
        root = Path(existing).expanduser().absolute() if existing else (
            Path(os.environ.get('XDG_STATE_HOME') or str(Path.home()/'.local/state'))
            / 'devtool' / 'launcher-native')
        if target_root is not None and root.resolve().is_relative_to(target_root.resolve()):
            raise Refused('DEVTOOL_XDG_DIR_INSIDE_TARGET_REPO')
        if root.is_symlink():
            raise Refused('DEVTOOL_XDG_DIR_IS_SYMLINK')
        env['DEVTOOL_XDG_DIR'] = str(root)
    return env


def redact(text: str) -> str:
    text = re.sub(r'\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{12,}\b', '[REDACTED_TOKEN]', text)
    text = re.sub(r'(?i)(authorization\s*:\s*(?:bearer|basic))\s+[^\s]+', r'\1 [REDACTED]', text)
    text = re.sub(r'(?i)(https?://)[^\s/@]+(?::[^\s/@]*)?@', r'\1[REDACTED]@', text)
    return text


def exec_cmd(argv: list[str], *, stage: str, cwd: Path | None = None,
             devtool_root: Path | None = None, timeout: float = 120,
             live: bool = False, target_root: Path | None = None) -> str:
    """Bounded native subprocess. Never strip useful exceptions or synthesize CI output."""
    env = safe_env(devtool_root=devtool_root, target_root=target_root or cwd, color_output=live)
    if live:
        # Native stdout is attached to the operator terminal; cannot forge
        # `gh watch` output. Do not print argv, which might contain secrets.
        say('LAUNCHER_NATIVE_BEGIN stage='+stage)
        try:
            cp = subprocess.run(argv, cwd=cwd, env=env, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Refused(f'COMMAND_UNAVAILABLE_OR_TIMED_OUT stage={stage} cause={type(e).__name__}') from e
        if cp.returncode:
            raise Refused(f'NATIVE_COMMAND_FAILED stage={stage} rc={cp.returncode} (native output above)')
        say('LAUNCHER_NATIVE_END stage='+stage+' rc=0')
        return ''
    try:
        cp = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                            text=True, errors='replace', timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Refused(f'COMMAND_UNAVAILABLE_OR_TIMED_OUT stage={stage} cause={type(e).__name__}') from e
    if cp.returncode:
        diagnostic = cp.stderr.strip()
        if not diagnostic:
            # Some native Devtool paths emit a JSON error envelope on stdout.
            # Prefer its bounded error message over an arbitrary tail of a
            # large TUI/metadata payload, without losing the real stage/rc.
            try:
                obj=json.loads(cp.stdout)
                if isinstance(obj,dict):
                    diagnostic=str(obj.get('stderr') or (obj.get('result') or {}).get('stderr') or '')
            except (ValueError,TypeError):
                pass
        diagnostic=redact((diagnostic or cp.stdout)[-4000:]).strip()
        raise Refused(f'NATIVE_COMMAND_FAILED stage={stage} rc={cp.returncode}\n{diagnostic}')
    return cp.stdout


def git(root: Path, *parts: str, stage: str = 'git') -> str:
    return exec_cmd(['git', '-C', str(root), *parts], stage=stage, timeout=30).strip()


def repo_identity(root: Path, *, clean: bool, branch_required: bool = True) -> dict[str, str]:
    root = root.expanduser().absolute()
    if root.is_symlink() or not root.is_dir():
        raise Refused('REPOSITORY_MISSING_OR_SYMLINK')
    top = Path(git(root, 'rev-parse', '--show-toplevel', stage='git-top')).resolve()
    if top != root.resolve():
        raise Refused('SELECT_EXACT_GIT_WORKTREE_ROOT')
    sha = validate_sha(git(root, 'rev-parse', 'HEAD', stage='git-head'), 'HEAD')
    tree = validate_sha(git(root, 'rev-parse', 'HEAD^{tree}', stage='git-tree'), 'TREE')
    origin = canonical_remote(git(root, 'remote', 'get-url', 'origin', stage='git-origin'))
    branch = git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD', stage='git-branch') if branch_required else ''
    if clean and git(root, 'status', '--porcelain', '--untracked-files=all', stage='git-status'):
        raise Refused('DIRTY_WORKTREE_REFUSED; inspect with git status --short; no reset performed')
    return dict(repository=origin, branch=branch, source_sha=sha, source_tree=tree, root=str(root.resolve()))


def payload_contract(plan: dict[str, Any], folder: Path, target: Path) -> tuple[list[dict[str,str]], dict[str,bytes]]:
    if plan.get('schema_version') != 1 or not isinstance(plan.get('changes'), list):
        raise Refused('PLAN_MUST_USE_NATIVE_DEVTOOL_SCHEMA_VERSION_1')
    changes = plan['changes']
    if not 1 <= len(changes) <= MAX_FILES:
        raise Refused('PLAN_FILE_COUNT_INVALID')
    seen: set[str] = set()
    total = 0
    inventory: list[dict[str, str]] = []
    data: dict[str, bytes] = {}
    folder = folder.expanduser().resolve(strict=True)
    for c in changes:
        if not isinstance(c, dict) or set(c) != {'path','action'}:
            raise Refused('PLAN_ENTRY_MUST_HAVE_PATH_AND_ACTION_ONLY')
        path,action = safe_path(c['path']),c['action']
        if path in seen or action not in ('add','modify'):
            raise Refused('DUPLICATE_OR_UNSUPPORTED_PLAN_ACTION')
        seen.add(path)
        src=folder/path
        if src.is_symlink() or not src.is_file() or not src.resolve().is_relative_to(folder):
            raise Refused('PAYLOAD_MISSING_OR_SYMLINK '+path)
        if src.stat().st_size>MAX_ONE:
            raise Refused('PAYLOAD_FILE_TOO_LARGE '+path)
        content=src.read_bytes()
        total+=len(content)
        if total>MAX_TOTAL:
            raise Refused('PAYLOAD_TOTAL_TOO_LARGE')
        dst=target/path
        # Any pre-existing path component symlink is unsafe, including parent.
        for anc in [dst,*list(dst.parents)]:
            if anc==target.parent:break
            if anc.is_symlink():raise Refused('SYMLINK_TARGET_PATH '+path)
            if anc==target:break
        if action=='modify':
            if not dst.is_file():raise Refused('MODIFY_TARGET_MISSING '+path)
            pre=validate_sha(git(target,'rev-parse','HEAD:'+path,stage='preimage-'+path),'preimage:'+path)
        else:
            if dst.exists() or dst.is_symlink():raise Refused('ADD_TARGET_ALREADY_EXISTS '+path)
            pre=''
        inventory.append({'path':path,'action':action,'source_blob_sha':pre,'payload_sha256':digest(content)})
        data['payload/'+path]=content
    return inventory,data


def make_manifest(name: str, identity: dict[str,str], devtool: dict[str,str],
                  inventory: list[dict[str,str]], message: str,
                  pr_title: str, pr_body: str, base: str, schema_blob: str) -> dict[str,Any]:
    if not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{2,63}',name):
        raise Refused('BAD_LAUNCHER_NAME')
    if not message or not message.strip() or len(message)>240 or '\n' in message:
        raise Refused('BAD_COMMIT_MESSAGE')
    if not base or not re.fullmatch(r'[A-Za-z0-9_./-]{1,120}',base) or '..' in base:
        raise Refused('BAD_BASE_BRANCH')
    if not 1<=len(pr_title)<=160 or len(pr_body)>8000:
        raise Refused('PR_METADATA_INVALID')
    return {'schema':SCHEMA,'name':name,'source':{k:identity[k] for k in ('repository','branch','source_sha','source_tree')},
            'devtool':{'repository':devtool['repository'],'schema_blob':schema_blob,
                       'source_sha':devtool['source_sha'],'source_tree':devtool['source_tree'],
                       'module_blob':devtool['module_blob']},
            'plan':{'schema_version':1,'changes':[{'path':c['path'],'action':c['action']} for c in inventory]},
            'inventory':inventory,'delivery':{'base':base,'commit_message':message,'pr_title':pr_title,'pr_body':pr_body},
            'limitations':{'release_qualified':False,'device_qualified':False,'auto_merge_permitted':False,
                           'local_gradle_permitted':False}}


def verify_manifest(m: dict[str,Any]) -> None:
    if m.get('schema') != SCHEMA or not isinstance(m.get('source'),dict) or not isinstance(m.get('devtool'),dict):
        raise Refused('UNSUPPORTED_LAUNCHER_MANIFEST')
    source=m['source'];devtool=m['devtool']
    for key in ('source_sha','source_tree'):validate_sha(source.get(key),'source.'+key)
    for k in ('schema_blob','source_sha','source_tree','module_blob'):
        validate_sha(devtool.get(k),'devtool.'+k)
    for key in ('repository','branch'):
        if not isinstance(source.get(key),str) or not source.get(key):raise Refused('INVALID_SOURCE_'+key)
    if not GHREPO.fullmatch(source['repository']) or devtool.get('repository')!='Mikeyphw/devtool':
        raise Refused('INVALID_REPOSITORY_IDENTITY')
    inv=m.get('inventory');plan=m.get('plan')
    if not isinstance(inv,list) or not 1<=len(inv)<=MAX_FILES or not isinstance(plan,dict):
        raise Refused('INVENTORY_INVALID')
    if plan.get('schema_version')!=1 or not isinstance(plan.get('changes'),list):raise Refused('PLAN_INVALID')
    if [(z.get('path'),z.get('action')) for z in plan['changes']] != [(z.get('path'),z.get('action')) for z in inv]:
        raise Refused('PLAN_AND_INVENTORY_MISMATCH')
    seen=set()
    for c in inv:
        p=safe_path(c.get('path'))
        if p in seen or c.get('action') not in ('add','modify'):raise Refused('BAD_INVENTORY_ACTION')
        seen.add(p)
        if c['action']=='modify':validate_sha(c.get('source_blob_sha'),'preimage:'+p)
        elif c.get('source_blob_sha')!='':raise Refused('ADD_MUST_NOT_PIN_PREIMAGE')
        if not SHA256.fullmatch(str(c.get('payload_sha256',''))):raise Refused('INVALID_PAYLOAD_DIGEST '+p)
    if not isinstance(m.get('delivery'),dict) or not isinstance(m.get('limitations'),dict):raise Refused('DELIVERY_METADATA_INVALID')
    if any(m['limitations'].get(k) is not False for k in ('release_qualified','device_qualified','auto_merge_permitted','local_gradle_permitted')):
        raise Refused('FORBIDDEN_QUALIFICATION_PROMISE')


def read_embedded_package(package: Path) -> tuple[dict[str,Any],dict[str,bytes]]:
    if not zipfile.is_zipfile(package):raise Refused('NOT_A_PYTHON_ZIPAPP')
    with zipfile.ZipFile(package) as z:
        infos=z.infolist();names=[i.filename for i in infos]
        if len(names)!=len(set(names)) or z.testzip() is not None:raise Refused('DUPLICATE_OR_CORRUPT_ZIPAPP')
        if '__main__.py' not in names  or 'manifest.json' not in names:
            raise Refused('MISSING_REQUIRED_PACKAGE_MODULE')
        if sum(i.file_size for i in infos)>MAX_TOTAL+300000:
            raise Refused('PACKAGE_TOO_LARGE')
        for info in infos:
            p=info.filename
            if p.endswith('/') or p.startswith('/') or '\\' in p or any(s in ('','..','.') for s in p.split('/')):
                raise Refused('INVALID_ZIP_MEMBER '+p)
            mode=(info.external_attr>>16)&0o170000
            if mode==stat.S_IFLNK:raise Refused('SYMLINK_ZIP_MEMBER '+p)
        m=json.loads(z.read('manifest.json'))
        verify_manifest(m)
        if z.read('__main__.py')!=bootstrap_content(m['devtool']).encode('utf-8'):
            raise Refused('ZIP_BOOTSTRAP_PIN_MISMATCH')
        expected={'payload/'+a['path'] for a in m['inventory']}
        actual={n for n in names if n.startswith('payload/')}
        if expected != actual:raise Refused('ZIP_INVENTORY_MISMATCH')
        data={n:z.read(n) for n in expected}
        for c in m['inventory']:
            if digest(data['payload/'+c['path']]) != c['payload_sha256']:
                raise Refused('ZIP_PAYLOAD_HASH_MISMATCH '+c['path'])
        if set(names) != {'__main__.py','manifest.json'} | expected:
            raise Refused('UNDECLARED_ZIP_MEMBER')
        # This is integrity, NOT a cryptographic signature. Source Git
        # pins + independently reviewed producer establish other authority.
        return m,data


def zip_bytes(files: dict[str,bytes]) -> bytes:
    buff=io.BytesIO()
    with zipfile.ZipFile(buff,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,blob in sorted(files.items()):
            zi=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
            zi.compress_type=zipfile.ZIP_DEFLATED
            zi.external_attr=(stat.S_IFREG|0o644)<<16
            z.writestr(zi,blob,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    return buff.getvalue()


def exclusive_write(dst:Path,content:bytes)->None:
    dst=dst.expanduser().absolute()
    if dst.is_symlink():raise Refused('OUTPUT_IS_SYMLINK')
    dst.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(dst,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(content);f.flush();os.fsync(f.fileno())
    except BaseException:
        dst.unlink(missing_ok=True)
        raise


def build_package(args:argparse.Namespace)->None:
    source_repo=Path(args.repo).expanduser().absolute()
    devtool_repo=Path(args.devtool_repo).expanduser().absolute()
    target=repo_identity(source_repo,clean=True)
    devtool=repo_identity(devtool_repo,clean=True)
    if devtool['repository']!='Mikeyphw/devtool':raise Refused('WRONG_DEVTOOL_REPOSITORY')
    devtool['module_blob']=validate_sha(git(devtool_repo,'rev-parse','HEAD:devtool/launcher_contract_v3.py'), 'devtool.module_blob')
    schema_blob=validate_sha(git(devtool_repo,'rev-parse','HEAD:devtool/core_cli_schema.json'), 'schema_blob')
    if not (devtool_repo/'devtool/core_cli_schema.json').is_file():raise Refused('DEVTOOL_CLI_SCHEMA_MISSING')
    with Path(args.plan).open('r',encoding='utf-8') as f:plan=json.load(f)
    inv,payload=payload_contract(plan,Path(args.payload),source_repo)
    manifest=make_manifest(args.name,target,devtool,inv,args.commit_message,
                           args.pr_title,args.pr_body,args.base,schema_blob)
    verify_manifest(manifest)
    files={'__main__.py':bootstrap_content(devtool).encode('utf-8'),
           'manifest.json':(json.dumps(manifest,sort_keys=True,indent=2,ensure_ascii=False)+'\n').encode(),
           **payload}
    raw=zip_bytes(files)
    # Test the generated bytes before publication.
    with tempfile.TemporaryDirectory() as t:
        tmp=Path(t)/'tmp.pyz';tmp.write_bytes(raw)
        found,data=read_embedded_package(tmp)
        if found!=manifest or data!=payload:raise Refused('BUILT_PACKAGE_ROUNDTRIP_MISMATCH')
    output=Path(args.output)
    if output.suffix!='.pyz' or output.resolve().is_relative_to(source_repo.resolve()):
        raise Refused('OUTPUT_MUST_BE_PYZ_OUTSIDE_TARGET_REPO')
    exclusive_write(output,raw)
    say('LAUNCHER_V3_PACKAGE_PASS',str(output.resolve()),'sha256='+digest(raw))
    say('LAUNCHER_V3_PIN',manifest['source']['source_sha'],manifest['source']['source_tree'])
    say('LAUNCHER_V3_NO_GITHUB_MUTATION; next: cd TARGET && python3 -B package.pyz apply --approve-remote')


def check_source(repo:Path,m:dict[str,Any])->dict[str,str]:
    actual=repo_identity(repo,clean=False)
    source=m['source']
    for k in ('repository','branch','source_sha','source_tree'):
        if actual[k]!=source[k]:
            raise Refused(f'SOURCE_{k.upper()}_MISMATCH expected={source[k]} actual={actual[k]}; select correct worktree or rebuild; NO_RESET')
    if git(repo,'status','--porcelain','--untracked-files=all',stage='source-clean'):
        raise Refused('DIRTY_WORKTREE_REFUSED; inspect with git status --short; no reset performed')
    for c in m['inventory']:
        p=c['path'];dst=repo/p
        for anc in [dst,*list(dst.parents)]:
            if anc==repo.parent:break
            if anc.is_symlink():raise Refused('SYMLINK_TARGET_PATH '+p)
            if anc==repo:break
        if c['action']=='modify':
            actual_pre=git(repo,'rev-parse','HEAD:'+p,stage='preimage-'+p)
            if actual_pre!=c['source_blob_sha']:
                raise Refused('SOURCE_BLOB_DRIFT path='+p+' expected='+c['source_blob_sha']+' actual='+actual_pre)
            if not dst.is_file():raise Refused('SOURCE_FILE_MISSING '+p)
        else:
            if dst.exists() or dst.is_symlink():raise Refused('ALREADY_APPLIED_OR_CONFLICT path='+p+'; use status, do not replay')
    return actual




def resolve_target_path(requested: str | None, manifest: dict[str,Any], *, cwd: Path | None = None) -> Path:
    # Only resolve the current Git worktree; never guess among user projects.
    location=Path(requested).expanduser().absolute() if requested else (cwd or Path.cwd()).absolute()
    if location.is_symlink():raise Refused('TARGET_LOCATION_SYMLINK_REFUSED')
    if requested:return location
    if not location.is_dir():raise Refused('TARGET_WORKTREE_NOT_FOUND '+str(location))
    root=Path(git(location,'rev-parse','--show-toplevel',stage='target-root')).resolve()
    if requested and root!=location.resolve():
        raise Refused('TARGET_EXPLICIT_PATH_NOT_WORKTREE_ROOT '+str(root))
    identity=repo_identity(root,clean=False)
    source=manifest['source']
    if identity['repository']!=source['repository'] or identity['branch']!=source['branch']:
        raise Refused('AUTO_TARGET_WRONG_REPOSITORY_OR_BRANCH expected='+source['repository']+
                      '/'+source['branch']+' actual='+identity['repository']+'/'+identity['branch']+
                      '; cd into the pinned worktree or pass --repo')
    say('LAUNCHER_V3_TARGET_BOUND '+str(root))
    return root


def runtime_candidates(target:Path, pinned:dict[str,str], requested: str | None = None) -> list[Path]:
    # Bounded read-only candidate inventory.
    home=Path.home()
    state=Path(os.environ.get('XDG_STATE_HOME') or str(home/'.local/state')).expanduser()
    options=[]
    if requested:options.append(Path(requested).expanduser())
    if os.environ.get('DEVTOOL_REPO'):options.append(Path(os.environ['DEVTOOL_REPO']).expanduser())
    options.extend([state/'devtool'/'launcher-runtimes'/('runtime-'+pinned['source_sha']),
                    target,home/'Code'/'devtool',home/'Code'/'dev',home/'Code'/'devtool-launcher-v3'])
    code=home/'Code'
    if code.is_dir() and not code.is_symlink():
        for entry in sorted(code.iterdir())[:96]:
            if 'devtool' in entry.name.lower() and entry.is_dir() and not entry.is_symlink():
                options.append(entry)
    unique=[];seen=set()
    for option in options:
        path=option.absolute()
        if path in seen or not path.is_dir() or path.is_symlink():continue
        seen.add(path);unique.append(path)
    return unique


def discover_runtime(target:Path, pinned:dict[str,str], requested: str | None = None)->Path:
    # Find a trusted checkout to feed the exact-runtime resolver.
    if requested:return Path(requested).expanduser().absolute()
    bases=runtime_candidates(target,pinned)
    for base in bases:
        if _same_pinned_checkout(base,pinned):
            say('LAUNCHER_V3_DISCOVER_EXACT_RUNTIME '+str(base))
            return base
    for base in bases:
        try:
            candidate=repo_identity(base,clean=True,branch_required=False)
        except (Refused,ValueError,OSError):continue
        if candidate['repository']==pinned['repository']:
            say('LAUNCHER_V3_DISCOVER_TRUSTED_RUNTIME '+str(base))
            return base
    raise Refused('NO_TRUSTED_DEVTOOL_CHECKOUT: set DEVTOOL_REPO or supply --devtool-repo; no source import')


def _same_pinned_checkout(root: Path, pinned: dict[str, str]) -> bool:
    """Read-only identity probe. Never accept a coincidental SHA in another repo."""
    try:
        identity=repo_identity(root,clean=True,branch_required=False)
        return (identity['repository']==pinned['repository'] and
                identity['source_sha']==pinned['source_sha'] and
                identity['source_tree']==pinned['source_tree'])
    except (Refused, OSError, ValueError):
        return False


def _branch_ref(branch: str) -> str:
    if not isinstance(branch,str) or not branch or branch.startswith('-'):
        raise Refused('AUTO_FF_INVALID_BRANCH')
    # Git is the authority on legal refnames; no interpolated shell commands.
    candidate='refs/heads/'+branch
    cp=subprocess.run(['git','check-ref-format',candidate],
                      capture_output=True,text=True,env=safe_env(),timeout=15)
    if cp.returncode:
        raise Refused('AUTO_FF_INVALID_BRANCH')
    return candidate


def _safe_fast_forward(root:Path, pinned:dict[str,str], *, label:str, enabled:bool=True)->None:
    """Fetch only the named branch and move it only to the exact pinned SHA.

    No `git pull`, tracking configuration, ref overwrite, rebase, branch
    switch, or network request when the checkout already matches. A fetch
    may update remote-tracking metadata; the exact pinned commit must equal FETCH_HEAD.
    """
    current=repo_identity(root,clean=True)
    if (current['repository']!=pinned['repository'] or
            current['branch']!=pinned['branch']):
        raise Refused('AUTO_FF_WRONG_ORIGIN_OR_BRANCH label='+label)
    if current['source_sha']==pinned['source_sha']:
        if current['source_tree']!=pinned['source_tree']:
            raise Refused('AUTO_FF_SAME_HEAD_TREE_MISMATCH label='+label)
        return
    if not enabled:
        raise Refused('AUTO_FF_DISABLED label='+label+' expected='+pinned['source_sha']+
                      ' actual='+current['source_sha'])
    _branch_ref(pinned['branch'])
    target=pinned['source_sha']
    say('LAUNCHER_V3_AUTO_FETCH label='+label+' branch='+pinned['branch']+' expected='+target[:12])
    # A source-bound fetch works even when the local feature branch has no
    # upstream, and doesn't rewrite its remote-tracking ref.
    exec_cmd(['git','-C',str(root),'fetch','--no-tags','--no-recurse-submodules',
              'origin','refs/heads/'+pinned['branch']],
             stage='auto-fetch-'+label,timeout=100,live=True)
    remote=validate_sha(git(root,'rev-parse','FETCH_HEAD',stage='auto-fetched-head'),
                        'FETCH_HEAD')
    if remote!=target:
        raise Refused('AUTO_FF_REMOTE_NOT_PINNED label='+label+
                      ' expected='+target+' fetched='+remote+'; no local branch changed')
    ancestor=subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',
                              current['source_sha'],target],
                             capture_output=True,env=safe_env(),timeout=20)
    if ancestor.returncode!=0:
        raise Refused('AUTO_FF_DIVERGED_OR_UNPROVEN label='+label+'; no merge attempted')
    if repo_identity(root,clean=True)['source_sha']!=current['source_sha']:
        raise Refused('AUTO_FF_SOURCE_RACE label='+label)
    exec_cmd(['git','-C',str(root),'merge','--ff-only',target],
             stage='auto-fast-forward-'+label,timeout=70,live=True)
    finished=repo_identity(root,clean=True)
    if finished['source_sha']!=target or finished['source_tree']!=pinned['source_tree']:
        raise Refused('AUTO_FF_RESULT_UNVERIFIED label='+label)
    say('LAUNCHER_V3_FAST_FORWARD_PASS label='+label+' head='+target[:12])


def _linked_worktrees(root:Path)->list[Path]:
    lines=git(root,'worktree','list','--porcelain',stage='discover-worktrees').splitlines()
    candidates=[]
    for line in lines:
        if line.startswith('worktree '):
            path=Path(line[len('worktree '):]).absolute()
            if path not in candidates: candidates.append(path)
    return candidates[:48]


def _runtime_cache_root(target:Path)->Path:
    # An independent detached worktree remains bound to the *original*
    # runtime SHA after applying the overlay to the target source worktree.
    root=Path(os.environ.get('XDG_STATE_HOME') or str(Path.home()/'.local/state'))
    location=root.expanduser().absolute()/'devtool'/'launcher-runtimes'
    if location.resolve().is_relative_to(target.resolve()):
        raise Refused('PINNED_RUNTIME_CACHE_INSIDE_TARGET')
    for p in (location,location.parent):
        if p.is_symlink():raise Refused('PINNED_RUNTIME_CACHE_SYMLINK')
    location.mkdir(parents=True,exist_ok=True,mode=0o700)
    if location.stat().st_mode & 0o077:
        raise Refused('PINNED_RUNTIME_CACHE_NOT_PRIVATE')
    return location


def resolve_runtime(target:Path, requested:Path,
                    pinned:dict[str,str], *, allow_ff:bool=True)->Path:
    """Select a stable exact pinned runtime; do not rewrite feature branches.

    Prefer a separately pinned existing worktree, then FF an exact `main`
    checkout when safe. If the caller is on an older feature branch, fetch
    pinned `origin/main` only as needed and create a private detached worktree.
    The private runtime survives the overlay's eventual target commit.
    """
    if _same_pinned_checkout(requested,pinned) and requested.resolve()!=target.resolve():
        return requested
    # The provided runtime can be on an older independent branch: observe its
    # Git identity without assuming branch tracking. `target` can be a consumer
    # and must never be used to fetch the Devtool origin in that case.
    try:
        old=repo_identity(requested,clean=True)
    except (Refused,OSError,ValueError):
        old=None
    if old is not None and old['repository']==pinned['repository']:
        base=requested
    elif _same_pinned_checkout(target,pinned):
        base=target
    else:
        raise Refused('NO_TRUSTED_DEVTOOL_CHECKOUT: supply a clean linked Devtool worktree; no branch reset')
    for candidate in _linked_worktrees(base):
        if candidate.resolve()!=target.resolve() and _same_pinned_checkout(candidate,pinned):
            say('LAUNCHER_V3_REUSE_PINNED_RUNTIME '+str(candidate))
            return candidate
    if base.resolve()==requested.resolve() and old and old['branch']=='main' and allow_ff:
        # Only update main when the remote points to the exact frozen commit.
        try:
            _safe_fast_forward(requested,{**pinned,'branch':'main'},
                               label='devtool-runtime',enabled=True)
            if _same_pinned_checkout(requested,pinned) and requested.resolve()!=target.resolve():
                return requested
        except Refused as e:
            say('LAUNCHER_V3_RUNTIME_FF_UNAVAILABLE '+redact(str(e)))
    dest=_runtime_cache_root(target)/('runtime-'+pinned['source_sha'])
    if dest.exists() or dest.is_symlink():
        if not _same_pinned_checkout(dest,pinned):
            raise Refused('CACHED_DEVTOOL_RUNTIME_DRIFT: inspect '+str(dest))
        say('LAUNCHER_V3_REUSE_PINNED_RUNTIME '+str(dest))
        return dest
    # Prefer already fetched exact object. If absent, ask Git for remote
    # main without changing any local branch or remote-tracking refs.
    def pinned_commit_available() -> bool:
        cp=subprocess.run(['git','-C',str(base),'cat-file','-e',
                           pinned['source_sha']+'^{commit}'],env=safe_env(),
                          capture_output=True,timeout=15)
        if cp.returncode:return False
        try:
            tree=git(base,'rev-parse',pinned['source_sha']+'^{tree}',stage='pinned-tree')
        except Refused:
            return False
        return tree==pinned['source_tree']
    if not pinned_commit_available():
        if not allow_ff:
            raise Refused('PINNED_RUNTIME_OBJECT_MISSING_AUTO_FETCH_DISABLED')
        say('LAUNCHER_V3_AUTO_FETCH label=pinned-runtime-base branch=main expected='+pinned['source_sha'][:12])
        exec_cmd(['git','-C',str(base),'fetch','--no-tags','--no-recurse-submodules',
                  'origin','refs/heads/main'],stage='auto-fetch-pinned-runtime',
                 timeout=100,live=True)
        remote=validate_sha(git(base,'rev-parse','FETCH_HEAD',stage='pinned-fetched-head'),'FETCH_HEAD')
        # main may have advanced after the launcher was built. Fetching it
        # must not repin the launcher; accept only the original SHA/tree if
        # Git proves that commit is reachable from freshly fetched main.
        ancestry=subprocess.run(['git','-C',str(base),'merge-base','--is-ancestor',
                                 pinned['source_sha'],remote],env=safe_env(),
                                capture_output=True,timeout=20)
        if not pinned_commit_available() or ancestry.returncode:
            raise Refused('PINNED_RUNTIME_REMOTE_MAIN_DRIFT: pinned source is not in fetched main history; no checkout modified')
    say('LAUNCHER_V3_CREATE_PINNED_RUNTIME '+str(dest))
    exec_cmd(['git','-C',str(base),'worktree','add','--detach',str(dest),pinned['source_sha']],
             stage='native-pinned-runtime-worktree',timeout=90,live=True)
    if not _same_pinned_checkout(dest,pinned):
        raise Refused('NEW_PINNED_RUNTIME_IDENTITY_MISMATCH')
    say('LAUNCHER_V3_PINNED_RUNTIME_READY '+str(dest))
    return dest



def prepare_worktrees(repo:Path,devtool_root:Path,m:dict[str,Any],
                      *,allow_ff:bool=True, operation:str='check')->tuple[Path,Path]:
    pinned_src=m['source']
    if operation in ('check','apply'):
        actual=repo_identity(repo,clean=True)
        if actual['source_sha']!=pinned_src['source_sha']:
            # Replaying after a completed commit must refuse *before* any
            # network fetch, even when the feature branch was never pushed.
            # Adding the same paths is a particularly strong replay signal.
            installed=[c['path'] for c in m['inventory']
                       if c['action']=='add' and (repo/c['path']).exists()]
            if installed:
                raise Refused('ALREADY_APPLIED_OR_CONFLICT paths='+','.join(installed[:5])+
                              '; use status/resume, never replay or fetch')
            # If the pin is a known ancestor of current HEAD, source is
            # already ahead: do not attempt to rewind or fetch older refs.
            reachable=subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',
                                      pinned_src['source_sha'],actual['source_sha']],
                                     capture_output=True,env=safe_env(),timeout=20)
            if reachable.returncode==0:
                raise Refused('SOURCE_ALREADY_ADVANCED_NO_REPLAY expected='+
                              pinned_src['source_sha']+' actual='+actual['source_sha'])
            _safe_fast_forward(repo,pinned_src,label='target-source',enabled=allow_ff)
        check_source(repo,m)
    else:
        # Source moves after a committed apply. Never rewind it to the old
        # preimage while inspecting its durable session or running CI.
        actual=repo_identity(repo,clean=False)
        if actual['repository']!=pinned_src['repository'] or actual['branch']!=pinned_src['branch']:
            raise Refused('POST_APPLY_WORKTREE_IDENTITY_MISMATCH')
    pinned_dev={**m['devtool'],'branch':'main'}
    # On generated v3 packages the original runtime can be a feature branch;
    # the exact SHA+tree is still the authority for selection.
    if pinned_dev.get('source_sha') is None or pinned_dev.get('source_tree') is None:
        raise Refused('DEVTOOL_RUNTIME_PINS_REQUIRED')
    bound=resolve_runtime(repo,devtool_root,pinned_dev,allow_ff=allow_ff)
    return repo,bound


def runtime_probe(repo:Path,devtool_root:Path,m:dict[str,Any])->None:
    dev=repo_identity(devtool_root,clean=False)
    # Native LIFE receipts are retained, not deleted or silently ignored.
    verify_postcommit_status(devtool_root)
    if dev['repository'] != 'Mikeyphw/devtool':raise Refused('DEVTOOL_ORIGIN_MISMATCH')
    for k, actual in (('source_sha',dev['source_sha']),('source_tree',dev['source_tree'])):
        if actual != m['devtool'][k]:
            raise Refused('DEVTOOL_RUNTIME_'+k.upper()+'_DRIFT expected='+m['devtool'][k]+' actual='+actual)
    module=git(devtool_root,'rev-parse','HEAD:devtool/launcher_contract_v3.py')
    if module != m['devtool']['module_blob']:
        raise Refused('DEVTOOL_LAUNCHER_MODULE_DRIFT')
    schema=git(devtool_root,'rev-parse','HEAD:devtool/core_cli_schema.json')
    if schema!=m['devtool']['schema_blob']:
        raise Refused('DEVTOOL_CLI_SCHEMA_DRIFT expected='+m['devtool']['schema_blob']+' actual='+schema+'; rebuild launcher after reviewing CLI changes')
    code='import devtool; from pathlib import Path; print(Path(devtool.__file__).resolve())'
    actual=exec_cmd([sys.executable,'-B','-c',code],stage='runtime-source-import',
                    cwd=devtool_root,devtool_root=devtool_root,target_root=repo,timeout=30).strip()
    expected=str((devtool_root/'devtool/__init__.py').resolve())
    if actual!=expected:raise Refused('WRONG_PYTHON_DEVTOOL_MODULE expected='+expected+' actual='+actual)
    # CLI schema blob + exact module/runtime SHA already bind all commands.
    # Probing every subcommand starts a fresh (expensive on Android) Devtool
    # process 15 times and can turn a read-only preflight into a 2-minute hang.
    # Probe a minimal representative set; every requested native operation is
    # independently parsed by its real CLI at invocation time.
    for command in (('delivery','adoption','scaffold'),('validate','overlay')):
        native(repo,devtool_root,*command,'--help',stage='cli-help-'+'-'.join(command),timeout=45)
    say('LAUNCHER_V3_DEVTOOL_BOUND module='+actual+' schema_pinned=yes native_probes=2')


def native(repo:Path,devtool_root:Path,*parts:str,stage:str,timeout:float=120,
           live:bool=False)->str:
    return exec_cmd([sys.executable,'-B','-m','devtool','--repo',str(repo),*parts],
                    stage=stage,cwd=devtool_root,devtool_root=devtool_root,target_root=repo,timeout=timeout,live=live)


def native_json(repo:Path,devtool_root:Path,*parts:str,stage:str)->dict[str,Any]:
    s=native(repo,devtool_root,*parts,'--json',stage=stage,timeout=180)
    try:
        obj=json.loads(s)
        if isinstance(obj,dict) and (obj.get('ok') is False or
                obj.get('exit_code') not in (None,0)):
            raise Refused('DEVTOOL_MACHINE_RECEIPT_NON_SUCCESS stage='+stage)
        if isinstance(obj,dict) and isinstance(obj.get('data'),dict):obj=obj['data']
    except ValueError as exc:
        raise Refused('DEVTOOL_MACHINE_RECEIPT_INVALID stage='+stage+' output='+redact(s[-1500:])) from exc
    if not isinstance(obj,dict):raise Refused('DEVTOOL_RECEIPT_NOT_OBJECT stage='+stage)
    return obj


def private_store() -> Path:
    # XDG_STATE_HOME is safer than assuming Termux external /sdcard (no locking).
    path=Path(os.environ.get('XDG_STATE_HOME') or str(Path.home()/'.local/state'))/'devtool'/'launchers'
    if path.is_symlink():raise Refused('STATE_STORE_SYMLINK')
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    if (path.stat().st_mode&0o077):raise Refused('STATE_STORE_NOT_PRIVATE_0700')
    return path


def scaffold(repo:Path,devtool_root:Path,m:dict[str,Any],payload:dict[str,bytes]) -> Path:
    with tempfile.TemporaryDirectory(prefix='devtool-launcher-v3-') as tmp:
        stage=Path(tmp);folder=stage/'payload';folder.mkdir()
        for name,blob in payload.items():
            dst=stage/name;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(blob)
        plan=stage/'plan.json';plan.write_text(json.dumps(m['plan'],sort_keys=True)+'\n')
        archive=private_store()/(m['name']+'-'+m['source']['source_sha'][:12]+'-'+uuid.uuid4().hex[:8]+'.zip')
        info=native_json(repo,devtool_root,'delivery','adoption','scaffold',
                         '--plan',str(plan),'--payload',str(folder),'--output',str(archive),
                         '--expected-head',m['source']['source_sha'],stage='native-scaffold')
        if (info.get('source_sha') != m['source']['source_sha'] or
                info.get('applied') is not False or not archive.is_file()):
            raise Refused('NATIVE_SCAFFOLD_RECEIPT_MISMATCH')
    native(repo,devtool_root,'validate','overlay',str(archive),stage='native-validate-overlay',timeout=330,live=True)
    say('LAUNCHER_V3_NATIVE_OVERLAY_VALIDATED '+str(archive))
    return archive


def session_check(repo:Path,devtool_root:Path,sid:str)->dict[str,Any]:
    if not SESSION32.fullmatch(sid):raise Refused('INVALID_SESSION_ID')
    record=native_json(repo,devtool_root,'delivery','session','status','--id',sid,stage='session-status')
    if Path(str(record.get('repo_root',''))).resolve()!=repo.resolve():raise Refused('SESSION_WORKTREE_MISMATCH')
    if record.get('repository') and record['repository']!=canonical_remote(git(repo,'remote','get-url','origin')):
        raise Refused('SESSION_REPOSITORY_MISMATCH')
    return record


def push_pr(repo:Path,devtool_root:Path,m:dict[str,Any],sid:str)->None:
    bound=session_check(repo,devtool_root,sid)
    if bound.get('head_sha') != git(repo,'rev-parse','HEAD'):
        raise Refused('SESSION_HEAD_DRIFT; inspect native delivery session report')
    native(repo,devtool_root,'delivery','session','push','--id',sid,'--yes',
           stage='native-push',timeout=300,live=True)
    # Devtool's journal handles uncertain outcomes; never auto-retry.
    native(repo,devtool_root,'delivery','session','pr','--id',sid,'--yes',
           '--title',m['delivery']['pr_title'],'--body',m['delivery']['pr_body'],
           stage='native-pr',timeout=300,live=True)
    say('LAUNCHER_V3_REMOTE_DELIVERED; hosted CI and merge are NOT qualified')


def observe_delivery_ci(repo:Path,devtool_root:Path,sid:str,*,checks:int=25,interval:int=8)->int:
    if checks<1 or checks>120 or interval<1 or interval>120:
        raise Refused('INVALID_BOUNDED_CI_POLLING')
    native(repo,devtool_root,'delivery','session','ci','--id',sid,'--checks',str(checks),
           '--interval',str(interval),stage='native-source-bound-ci',
           timeout=max(120,checks*interval+180),live=True)
    report=native_json(repo,devtool_root,'delivery','session','report','--id',sid,stage='native-ci-machine-receipt')
    ci=(report.get('receipt') or {}).get('ci_state') or (report.get('ci') or {}).get('state')
    say('LAUNCHER_V3_CI_STATE '+str(ci or 'UNKNOWN')+'; formal release NOT qualified')
    native(repo,devtool_root,'delivery','session','report','--id',sid,
           stage='native-saved-report',timeout=90,live=True)
    return 0 if ci in ('REQUIRED_CHECKS_PASSED','required_checks_passed') else 3


def verify_postcommit_status(repo:Path)->None:
    """Preserve native LIFE cleanup receipts; never delete operator evidence.

    Current Devtool persists a non-secret cleanup receipt under repo-local
    .devtool/private/... even with external DEVTOOL_XDG_DIR. This can appear
    as an untracked file after a successful apply. It is *not* an overlay source
    change and cannot be silently removed. All other dirt fails closed.
    """
    porcelain=git(repo,'status','--porcelain','--untracked-files=all',stage='postcommit-status')
    if not porcelain:return
    allowed=re.compile(r'\?\? \.devtool/private/cleanup/receipts/cleanup-receipt-[0-9a-f]{32}\.json\Z')
    for line in porcelain.splitlines():
        if not allowed.fullmatch(line):
            raise Refused('POST_COMMIT_UNREVIEWED_WORKTREE_DIRT '+redact(line))
        relative=line[3:]
        path=repo/relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size>128*1024:
            raise Refused('NATIVE_LIFE_RECEIPT_TYPE_UNVERIFIED')
        try:
            receipt=json.loads(path.read_text())
        except (UnicodeError,ValueError,OSError) as exc:
            raise Refused('NATIVE_LIFE_RECEIPT_UNREADABLE') from exc
        if not isinstance(receipt,dict) or not str(receipt.get('receipt_id','')).startswith('cleanup-receipt:'):
            raise Refused('NATIVE_LIFE_RECEIPT_SCHEMA_UNVERIFIED')
        say('LAUNCHER_V3_PRESERVED_DEVTOOL_LIFE_RECEIPT path='+relative+' (untracked; not deleted)')


def apply_package(repo:Path,devtool_root:Path,m:dict[str,Any],payload:dict[str,bytes],approve_remote:bool,
                  *,watch_ci:bool=False,checks:int=25,interval:int=8)->int:
    check_source(repo,m);runtime_probe(repo,devtool_root,m)
    archive=scaffold(repo,devtool_root,m,payload)
    native(repo,devtool_root,'apply','overlay',str(archive),'--yes','--commit',
           m['delivery']['commit_message'],'--commit-mode','new','--static-validate',
           '--keep-artifact',stage='native-atomic-apply-and-commit',timeout=800,live=True)
    after=repo_identity(repo,clean=False)
    verify_postcommit_status(repo)
    if after['source_sha']==m['source']['source_sha'] or after['branch']!=m['source']['branch']:
        raise Refused('POST_COMMIT_IDENTITY_NOT_PROVEN')
    for c in m['inventory']:
        new=repo/c['path']
        if not new.is_file() or digest(new.read_bytes())!=c['payload_sha256']:
            raise Refused('POST_COMMIT_PAYLOAD_MISMATCH '+c['path'])
    say('LAUNCHER_V3_COMMITTED source='+after['source_sha'])
    receipt=native_json(repo,devtool_root,'delivery','session','start',
                        '--base',m['delivery']['base'],'--head',m['source']['branch'],
                        '--overlay',str(archive),stage='native-session-start')
    sid=receipt.get('id')
    if receipt.get('head_sha')!=after['source_sha'] or not isinstance(sid,str) or not SESSION32.fullmatch(sid):
        raise Refused('DELIVERY_SESSION_IDENTITY_MISMATCH; use native delivery session status/report to recover')
    say('LAUNCHER_V3_SESSION_ID '+sid)
    say('LAUNCHER_V3_RESUME_COMMAND: python3 -B PACKAGE.pyz status --repo PATH --devtool-repo PATH --session-id '+sid)
    if approve_remote:
        push_pr(repo,devtool_root,m,sid)
        if watch_ci:
            return observe_delivery_ci(repo,devtool_root,sid,checks=checks,interval=interval)
    else:say('LAUNCHER_V3_LOCAL_ONLY; no push or PR was attempted')
    return 0


def runtime_command(args:argparse.Namespace,package:Path)->int:
    m,payload=read_embedded_package(package)
    op=args.command
    if op=='inspect':
        say(json.dumps({'name':m['name'],'schema':m['schema'],'source':m['source'],
                        'devtool':m['devtool'],'paths':[c['path'] for c in m['inventory']],
                        'limitations':m['limitations']},indent=2,sort_keys=True));return 0
    repo=resolve_target_path(getattr(args,'repo',None),m)
    devtool_root=discover_runtime(repo,m['devtool'],getattr(args,'devtool_repo',None))
    if op != 'inspect':
        repo,devtool_root=prepare_worktrees(repo,devtool_root,m,allow_ff=not getattr(args,'no_auto_ff',False),operation=op)
    if op=='check':
        check_source(repo,m);runtime_probe(repo,devtool_root,m)
        archive=scaffold(repo,devtool_root,m,payload)
        say('LAUNCHER_V3_READ_ONLY_NATIVE_VALIDATION_PASS archive='+str(archive));return 0
    if op=='apply':
        if getattr(args,'watch_ci',False) and not args.approve_remote:
            raise Refused('WATCH_CI_REQUIRES_APPROVE_REMOTE')
        return apply_package(repo,devtool_root,m,payload,args.approve_remote,
                             watch_ci=getattr(args,'watch_ci',False),
                             checks=getattr(args,'checks',25),interval=getattr(args,'interval',8))
    if op in ('diagnose','plan','capabilities','verify-artifact'):
        runtime_probe(repo,devtool_root,m)
        if op=='diagnose':
            result=native_json(repo,devtool_root,'overlay','recovery-plan',stage='native-recovery-plan')
            say(json.dumps(result,indent=2,sort_keys=True))
        elif op=='capabilities':
            result=native_json(repo,devtool_root,'capabilities',stage='native-capabilities')
            say(json.dumps(result,indent=2,sort_keys=True))
        elif op=='plan':
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',args.workflow):raise Refused('INVALID_WORKFLOW_NAME')
            argv=['plan',args.workflow]
            if args.target:
                if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',args.target):raise Refused('INVALID_TARGET')
                argv += ['--target',args.target]
            result=native_json(repo,devtool_root,*argv,stage='native-dag-plan')
            say(json.dumps(result,indent=2,sort_keys=True))
        elif op=='verify-artifact':
            receipt=Path(args.receipt).expanduser().absolute()
            if receipt.is_symlink() or not receipt.is_file():raise Refused('ARTIFACT_RECEIPT_INVALID')
            argv=['artifacts','verify','--receipt',str(receipt),'--repository',m['source']['repository']]
            if args.run_id is not None:
                if args.run_id<1:raise Refused('INVALID_RUN_ID')
                argv+=['--run-id',str(args.run_id)]
            if args.attempt is not None:
                if args.attempt<1:raise Refused('INVALID_RUN_ATTEMPT')
                argv+=['--attempt',str(args.attempt)]
            result=native_json(repo,devtool_root,*argv,stage='native-artifact-verify')
            say(json.dumps(result,indent=2,sort_keys=True))
        return 0
    if op in ('status','report','ci','deliver','resume'):
        sid=args.session_id
        runtime_probe(repo,devtool_root,m)
        bound=session_check(repo,devtool_root,sid)
        say('LAUNCHER_V3_SESSION_BOUND '+sid+' stage='+str(bound.get('stage',bound.get('state','UNKNOWN'))))
        if op=='resume':
            result=native_json(repo,devtool_root,'delivery','session','resume','--id',sid,stage='native-session-resume')
            say(json.dumps(result,indent=2,sort_keys=True));return 0
        if op=='status':
            say(json.dumps(bound,indent=2,sort_keys=True));return 0
        if op=='report':
            native(repo,devtool_root,'delivery','session','report','--id',sid,
                   stage='native-saved-report',timeout=90,live=True)
            return 0
        if op=='deliver':
            if not args.approve_remote:raise Refused('REMOTE_DELIVERY_REQUIRES_EXPLICIT_APPROVAL')
            push_pr(repo,devtool_root,m,sid);return 0
        return observe_delivery_ci(repo,devtool_root,sid,checks=args.checks,interval=args.interval)
    raise Refused('UNKNOWN_RUNTIME_OPERATION')


def bootstrap_content(devtool:dict[str,str])->str:
    """Minimal immutable bootstrap: exact Git identity before imported module."""
    pins={'source_sha':devtool['source_sha'],'source_tree':devtool['source_tree'],'module_blob':devtool['module_blob']}
    code='''# Minimal v3 manifest-only zipapp bootstrap. No Devtool engine is bundled.
import json, os, pathlib, re, subprocess, sys, zipfile
PINS = _PINS_
argv = sys.argv[1:]
if argv == ['inspect']:
    with zipfile.ZipFile(sys.argv[0]) as z:
        data=json.loads(z.read('manifest.json'))
    print(json.dumps({k:data[k] for k in ('schema','name','source','devtool','inventory','limitations')},indent=2,sort_keys=True))
    raise SystemExit(0)
no_auto_ff='--no-auto-ff' in argv
requested=argv[argv.index('--devtool-repo')+1] if '--devtool-repo' in argv and argv.index('--devtool-repo')+1<len(argv) else ''
root=pathlib.Path(requested).expanduser().absolute() if requested else None
env=dict(os.environ, GIT_NO_REPLACE_OBJECTS='1',GIT_TERMINAL_PROMPT='0', PYTHONDONTWRITEBYTECODE='1')
env.update(DEVTOOL_EXECUTION_PRESENTATION='plain',DEVTOOL_JOB_UI='plain',
           DEVTOOL_TEXTUAL_APPLY_HOST='0',DEVTOOL_TEXTUAL_EXECUTION_HOST='0')
if sys.stdout.isatty() and 'NO_COLOR' not in env and env.get('CLICOLOR')!='0' and env.get('TERM')!='dumb':
    env.setdefault('FORCE_COLOR','1')
def git(path,*args,timeout=30):
    p=subprocess.run(['git','-C',str(path),*args],env=env,capture_output=True,text=True,timeout=timeout)
    if p.returncode:
        raise RuntimeError('native Git operation failed: '+str(args[0]))
    return p.stdout.strip()
def canonical(path):
    try:
        origin=git(path,'remote','get-url','origin')
        m=re.fullmatch(r'(?:git@github\\.com:|https://github\\.com/|ssh://git@github\\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\\.git)?/?',origin,re.I)
        return m is not None and m.group(1)=='Mikeyphw/devtool'
    except (OSError,RuntimeError,subprocess.TimeoutExpired):
        return False
def clean(path):
    if path.is_symlink() or not path.is_dir() or not canonical(path):return False
    try:
        return (path.resolve()==pathlib.Path(git(path,'rev-parse','--show-toplevel')).resolve() and
                not git(path,'status','--porcelain','--untracked-files=all'))
    except (OSError,RuntimeError,subprocess.TimeoutExpired):
        return False
def exact(path):
    if not clean(path):return False
    try:
        return (git(path,'rev-parse','HEAD')==PINS['source_sha'] and
                git(path,'rev-parse','HEAD^{tree}')==PINS['source_tree'] and
                git(path,'rev-parse','HEAD:devtool/launcher_contract_v3.py')==PINS['module_blob'] and
                git(path,'hash-object','devtool/launcher_contract_v3.py')==PINS['module_blob'])
    except (OSError,RuntimeError,subprocess.TimeoutExpired):
        return False
# Source-bound target discovery is intentionally only from the caller's CWD.
# An arbitrary repo cannot be silently chosen from $HOME/Code.
if not zipfile.is_zipfile(sys.argv[0]):
    raise SystemExit('LAUNCHER_V3_REFUSED: runtime source_sha drift or invalid launcher container; no module import')
with zipfile.ZipFile(sys.argv[0]) as _package:
    _manifest=json.loads(_package.read('manifest.json'))
if '--repo' not in argv:
    try:
        target=pathlib.Path(git(pathlib.Path.cwd(),'rev-parse','--show-toplevel')).resolve()
        origin=git(target,'remote','get-url','origin')
        rm=re.fullmatch(r'(?:git@github[.]com:|https://github[.]com/|ssh://git@github[.]com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:[.]git)?/?',origin,re.I)
        if not rm or rm.group(1)!=_manifest['source']['repository'] or git(target,'symbolic-ref','--quiet','--short','HEAD')!=_manifest['source']['branch']:
            raise RuntimeError('wrong repository/branch')
    except (OSError,KeyError,RuntimeError,subprocess.TimeoutExpired) as e:
        raise SystemExit('LAUNCHER_V3_REFUSED: cd into exact target worktree or pass --repo ('+type(e).__name__+')')
    argv.extend(['--repo',str(target)])
else:
    target=pathlib.Path(argv[argv.index('--repo')+1]).expanduser().absolute()
if root is None:
    home=pathlib.Path.home()
    state=pathlib.Path(os.environ.get('XDG_STATE_HOME') or str(home/'.local/state')).expanduser()
    locations=[state/'devtool'/'launcher-runtimes'/('runtime-'+PINS['source_sha']),
               target,home/'Code'/'devtool',home/'Code'/'dev',home/'Code'/'devtool-launcher-v3']
    if os.environ.get('DEVTOOL_REPO'):locations.insert(0,pathlib.Path(os.environ['DEVTOOL_REPO']).expanduser())
    code=home/'Code'
    if code.is_dir() and not code.is_symlink():
        locations.extend(x for x in sorted(code.iterdir())[:96]
                         if 'devtool' in x.name.lower() and x.is_dir() and not x.is_symlink())
    seen=set();trusted=[]
    for candidate in locations:
        candidate=candidate.absolute()
        if candidate in seen or candidate.is_symlink() or not candidate.is_dir():continue
        seen.add(candidate)
        if exact(candidate):root=candidate;break
        if clean(candidate):trusted.append(candidate)
    if root is None:
        root=next((x for x in trusted if canonical(x)),None)
    if root is None:
        raise SystemExit('LAUNCHER_V3_REFUSED: cannot discover trusted Devtool runtime; set DEVTOOL_REPO or pass --devtool-repo')
    argv.extend(['--devtool-repo',str(root)])
if not exact(root):
    if not clean(root):
        raise SystemExit('LAUNCHER_V3_REFUSED: runtime source_sha drift or unclean/wrong checkout; no module import')
    try:
        worktrees=git(root,'worktree','list','--porcelain').splitlines()
        for line in worktrees:
            if line.startswith('worktree '):
                sibling=pathlib.Path(line[9:]).absolute()
                if exact(sibling):
                    root=sibling
                    print('LAUNCHER_V3_REUSE_PINNED_RUNTIME '+str(root),flush=True)
                    break
    except (OSError,RuntimeError,subprocess.TimeoutExpired) as e:
        raise SystemExit('LAUNCHER_V3_REFUSED: cannot inspect worktrees: '+type(e).__name__)
if not exact(root):
    # Only main may FF to its own frozen HEAD. An old feature branch is
    # NEVER moved to main and needs no upstream configured for this operation.
    try:
        branch=git(root,'symbolic-ref','--quiet','--short','HEAD')
        if branch=='main' and not no_auto_ff:
            print('LAUNCHER_V3_AUTO_FETCH branch=main expected='+PINS['source_sha'][:12],flush=True)
            p=subprocess.run(['git','-C',str(root),'fetch','--no-tags','--no-recurse-submodules','origin','refs/heads/main'],env=env,timeout=100)
            if p.returncode:
                raise SystemExit('LAUNCHER_V3_REFUSED: fetch failed; no local branch changed')
            if git(root,'rev-parse','FETCH_HEAD')==PINS['source_sha']:
                p=subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor','HEAD',PINS['source_sha']],env=env,timeout=20)
                if p.returncode==0 and clean(root):
                    p=subprocess.run(['git','-C',str(root),'merge','--ff-only',PINS['source_sha']],env=env,timeout=70)
                    if p.returncode!=0:raise SystemExit('LAUNCHER_V3_REFUSED: fast-forward failed')
        if not exact(root):
            home=pathlib.Path(os.environ.get('XDG_STATE_HOME') or str(pathlib.Path.home()/'.local/state')).expanduser().absolute()
            dest_root=home/'devtool'/'launcher-runtimes'
            if dest_root.resolve().is_relative_to(root.resolve()) or dest_root.is_symlink() or dest_root.parent.is_symlink():
                raise SystemExit('LAUNCHER_V3_REFUSED: invalid runtime-cache path')
            dest_root.mkdir(parents=True,mode=0o700,exist_ok=True)
            if dest_root.stat().st_mode & 0o077:
                raise SystemExit('LAUNCHER_V3_REFUSED: runtime cache must be private 0700')
            dest=dest_root/('runtime-'+PINS['source_sha'])
            if dest.exists() or dest.is_symlink():
                if not exact(dest):raise SystemExit('LAUNCHER_V3_REFUSED: cached runtime drift')
            else:
                p=subprocess.run(['git','-C',str(root),'cat-file','-e',PINS['source_sha']+'^{commit}'],env=env,timeout=20)
                if p.returncode and not no_auto_ff:
                    print('LAUNCHER_V3_AUTO_FETCH_PINNED branch=main',flush=True)
                    p=subprocess.run(['git','-C',str(root),'fetch','--no-tags','--no-recurse-submodules','origin','refs/heads/main'],env=env,timeout=100)
                    if p.returncode:
                        raise SystemExit('LAUNCHER_V3_REFUSED: cannot fetch pinned main source')
                    remote=git(root,'rev-parse','FETCH_HEAD')
                    reachable=subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',
                                              PINS['source_sha'],remote],env=env,timeout=20)
                    if reachable.returncode:
                        raise SystemExit('LAUNCHER_V3_REFUSED: source pin absent from fetched main ancestry')
                if git(root,'rev-parse',PINS['source_sha']+'^{tree}')!=PINS['source_tree']:
                    raise SystemExit('LAUNCHER_V3_REFUSED: pinned source tree mismatch')
                print('LAUNCHER_V3_CREATE_PINNED_RUNTIME '+str(dest),flush=True)
                p=subprocess.run(['git','-C',str(root),'worktree','add','--detach',str(dest),PINS['source_sha']],env=env,timeout=90)
                if p.returncode:raise SystemExit('LAUNCHER_V3_REFUSED: pinned runtime worktree creation failed')
            root=dest
    except (OSError,RuntimeError,subprocess.TimeoutExpired) as e:
        raise SystemExit('LAUNCHER_V3_REFUSED: runtime resolution failure '+type(e).__name__)
if not exact(root):
    raise SystemExit('LAUNCHER_V3_REFUSED: runtime source_sha drift; no module import')
argv[argv.index('--devtool-repo')+1]=str(root)
# Ensure the source-bound parent import receives native presentation settings.
# Devtool's own subprocesses enforce them again independently.
for _key in ('DEVTOOL_EXECUTION_PRESENTATION','DEVTOOL_JOB_UI',
             'DEVTOOL_TEXTUAL_APPLY_HOST','DEVTOOL_TEXTUAL_EXECUTION_HOST'):
    os.environ[_key]=env[_key]
if 'FORCE_COLOR' in env and 'FORCE_COLOR' not in os.environ:
    os.environ['FORCE_COLOR']=env['FORCE_COLOR']
sys.argv[1:]=argv
sys.path.insert(0,str(root))
from devtool.launcher_contract_v3 import main
raise SystemExit(main())
'''
    return code.replace('_PINS_',repr(pins))


def make_parser(is_builder:bool)->argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=('Devtool Launcher Contract v3 builder' if is_builder else 'Devtool native-only source-bound launcher'))
    sub=p.add_subparsers(dest='command',required=True)
    if is_builder:
        sub.add_parser('self-test',help='Execute adversarial contract suite')
        q=sub.add_parser('build',help='Make immutable source-bound executable .pyz')
        for name in ('repo','devtool-repo','plan','payload','output','name','commit-message','pr-title'):
            q.add_argument('--'+name,required=True)
        q.add_argument('--base',default='main');q.add_argument('--pr-body',default='')
        return p
    sub.add_parser('inspect',help='Read-only embedded inventory, no local repo required')
    for command in ('check','apply','status','report','ci','deliver','resume','diagnose','plan','capabilities','verify-artifact'):
        q=sub.add_parser(command)
        q.add_argument('--repo',help='Optional target worktree (default: current Git worktree)')
        q.add_argument('--devtool-repo',help='Optional trusted Devtool checkout (auto-discovered and exactly pinned)')
        q.add_argument('--no-auto-ff',action='store_true',help='Disable automatic source-bound Git fetch/fast-forward')
        if command in ('status','report','ci','deliver','resume'):
            q.add_argument('--session-id',required=True,help='32-character durable Devtool delivery session ID')
        if command in ('apply','deliver'):
            q.add_argument('--approve-remote',action='store_true',help='Explicit push/PR permission; no merge/dispatch')
        if command in ('ci','apply'):
            q.add_argument('--checks',type=int,default=25)
            q.add_argument('--interval',type=int,default=8)
        if command=='apply':
            q.add_argument('--watch-ci',action='store_true',help='After approved push/PR, observe native required CI in this invocation')
        if command=='plan':
            q.add_argument('--target',required=False)
            q.add_argument('--workflow',default='validate')
        if command=='verify-artifact':
            q.add_argument('--receipt',required=True)
            q.add_argument('--run-id',type=int)
            q.add_argument('--attempt',type=int)
    return p


def main()->int:
    package=Path(sys.argv[0]).resolve()
    try:
        if zipfile.is_zipfile(package):
            with zipfile.ZipFile(package) as z:
                is_builder='manifest.json' not in z.namelist()
        else:
            is_builder=True
        args=make_parser(is_builder).parse_args()
        if args.command != 'inspect':
            banner(args.command, builder=is_builder)
        if is_builder:
            if args.command=='self-test':
                import unittest
                tests=Path(__file__).resolve().parent.parent/'tests'
                suite=unittest.defaultTestLoader.discover(str(tests),pattern='test_launcher_contract_v3.py')
                result=unittest.TextTestRunner(verbosity=2).run(suite)
                if not result.wasSuccessful() or result.testsRun<57:return 2
                say('LAUNCHER_V3_SELF_TESTS_PASS count='+str(result.testsRun));return 0
            if args.command=='build':build_package(args);return 0
        else:
            if args.command in ('ci','apply') and (args.checks<1 or args.checks>120 or args.interval<1 or args.interval>120):
                raise Refused('INVALID_BOUNDED_CI_POLLING')
            return runtime_command(args,package)
        raise Refused('UNKNOWN_OPERATION')
    except (Refused,ValueError,OSError,KeyError,zipfile.BadZipFile,json.JSONDecodeError) as exc:
        say('LAUNCHER_V3_REFUSED '+redact(str(exc)))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
