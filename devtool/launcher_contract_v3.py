"""Devtool Launcher Contract v3: source-bound, thin, restart-safe Python/Rich zipapps.

This module deliberately does not implement an overlay engine, a GitHub API
client, a scheduler, or a release authority. Devtool owns those operations.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import traceback
import uuid
import zipfile
from typing import Callable
from typing import Any
from .launcher_validation_contract import (
    ValidationContractError, inspect_delivery_plan, metadata_descriptors,
    verify_metadata_manifest, validation_execution_mode, MAX_TOTAL_HOOK_BYTES,
)

SCHEMA = 'devtool.launcher-contract.v3'
LAUNCHER_CONTRACT_VERSION = '3.2.0'
from .launcher_runtime_compat import (
    require_version, declared_version, IncompatibleLauncherRuntime)
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


class _FailureTranscript:
    """Private, non-truncated transcript; never triggers a clipboard write.

    This records launcher messages, captured machine child streams, and both
    native streams without changing the semantic authority of Devtool output.
    """
    def __init__(self, path: Path, fd: int):
        self.path = path
        self.fd = fd
        self.lock = threading.Lock()

    def append(self, value: bytes | str) -> None:
        raw = value.encode('utf-8', errors='replace') if isinstance(value, str) else value
        if not raw:
            return
        with self.lock:
            try:
                view = memoryview(raw)
                while view:
                    count = os.write(self.fd, view)
                    if count <= 0:
                        raise OSError('short transcript write')
                    view = view[count:]
            except OSError:
                # Logging is supplemental and must not alter native transaction
                # outcomes. The caller will not claim the log is complete.
                self.failed = True

    failed = False

    def close(self) -> None:
        os.close(self.fd)


_ACTIVE_TRANSCRIPT: _FailureTranscript | None = None


def _new_failure_transcript() -> _FailureTranscript | None:
    """Never put potentially sensitive raw diagnostics in external storage."""
    root = Path.home() / '.local' / 'state' / 'devtool' / 'launcher-logs'
    try:
        if root.is_symlink():
            return None
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (root.is_symlink() or not root.is_dir() or
                root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077):
            return None
        fd, name = tempfile.mkstemp(prefix='launcher-', suffix='.log', dir=root)
        return _FailureTranscript(Path(name), fd)
    except OSError:
        return None


class _TranscriptTee:
    """Tee in-process Python text; native subprocess streams use byte relay."""
    def __init__(self, stream: Any, transcript: _FailureTranscript):
        self.underlying = stream
        self.transcript = transcript

    def write(self, value: str) -> int:
        written = self.underlying.write(value)
        self.transcript.append(value)
        return written

    def flush(self) -> None:
        self.underlying.flush()

    def isatty(self) -> bool:
        return bool(self.underlying.isatty())

    def __getattr__(self, attr: str) -> Any:
        return getattr(self.underlying, attr)


def _stream_native_and_log(argv: list[str], *, cwd: Path | None,
                           env: dict[str,str], timeout: float) -> int:
    """Pass through the real native byte streams, recording each byte.

    No Textual, terminal emulator, command substitution or output filtering.
    A bounded child timeout is preserved. Inherited stdin is unchanged.
    """
    transcript = _ACTIVE_TRANSCRIPT
    assert transcript is not None
    with subprocess.Popen(argv,cwd=cwd,env=env,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE) as proc:
        def relay(pipe: Any, display: Any) -> None:
            try:
                base = display.underlying if isinstance(display,_TranscriptTee) else display
                dest = getattr(base, 'buffer', None)
                while True:
                    chunk = pipe.read1(65536)
                    if not chunk:
                        break
                    # Real subprocess stdout/stderr, streamed unchanged in full.
                    if dest is not None:
                        dest.write(chunk)
                        dest.flush()
                    else:
                        base.write(chunk.decode('utf-8',errors='replace'))
                        base.flush()
                    transcript.append(chunk)
            except (OSError, ValueError, RuntimeError):
                transcript.failed = True
        out_thread=threading.Thread(target=relay,args=(proc.stdout,sys.stdout),daemon=True)
        err_thread=threading.Thread(target=relay,args=(proc.stderr,sys.stderr),daemon=True)
        out_thread.start();err_thread.start()
        try:
            rc=proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill();proc.wait()
            raise
        finally:
            out_thread.join(timeout=10);err_thread.join(timeout=10)
            if out_thread.is_alive() or err_thread.is_alive():
                # Grandchildren that retain stdout pipes can prevent EOF.
                # Never claim an incomplete transcript contains the full log.
                transcript.failed = True
    return rc


def _with_failure_transcript(action: Callable[[],int]) -> int:
    """Report a copy command only for failures, never copy automatically."""
    global _ACTIVE_TRANSCRIPT
    transcript=_new_failure_transcript()
    original=_ACTIVE_TRANSCRIPT
    rc=2
    if transcript is None:
        try:
            return action()
        finally:
            # Do not suggest a nonexistent complete log.
            pass
    _ACTIVE_TRANSCRIPT=transcript
    try:
        with contextlib.redirect_stdout(_TranscriptTee(sys.stdout,transcript)), \
             contextlib.redirect_stderr(_TranscriptTee(sys.stderr,transcript)):
            try:
                rc=action()
                return rc
            except SystemExit as exc:
                if exc.code is None or exc.code == 0:
                    rc = 0
                else:
                    transcript.append(
                        'LAUNCHER_V3_UNHANDLED_EXCEPTION\n'
                        + traceback.format_exc()
                    )
                raise
            except BaseException:
                transcript.append('LAUNCHER_V3_UNHANDLED_EXCEPTION\n'+traceback.format_exc())
                raise
    finally:
        _ACTIVE_TRANSCRIPT=original
        transcript.close()
        if rc == 0:
            # Successful sessions require neither a clipboard prompt nor a
            # retained copy of potentially sensitive native output.
            transcript.path.unlink(missing_ok=True)
        elif transcript.failed:
            print('LAUNCHER_V3_FULL_FAILURE_LOG_UNAVAILABLE (transcript write failed)',file=sys.stderr,flush=True)
        else:
            print('LAUNCHER_V3_FULL_FAILURE_LOG '+str(transcript.path),file=sys.stderr,flush=True)
            print('LAUNCHER_V3_COPY_SUGGESTION (optional, no automatic clipboard write):',file=sys.stderr,flush=True)
            print('  termux-clipboard-set < '+shlex.quote(str(transcript.path)),file=sys.stderr,flush=True)
            print('Review the full log for credentials before copying or sharing.',file=sys.stderr,flush=True)


def banner(command: str, *, builder: bool = False) -> None:
    role = 'BUILDER' if builder else 'NATIVE DELIVERY'
    message = (f'Devtool · {role} · {command}\n'
               'Native source-bound delivery · exact provenance · durable sessions\n'
               'No merge, force push, device writes or release qualification')
    if _RICH is not None and _RichPanel is not None and 'NO_COLOR' not in os.environ and os.environ.get('CLICOLOR') != '0' and os.environ.get('TERM') != 'dumb':
        if _ACTIVE_TRANSCRIPT is not None:
            _ACTIVE_TRANSCRIPT.append(message+'\n')
        _RICH.print(_RichPanel(message, title='Devtool Python Launcher Contract', expand=False))
    else:
        say(message)
CLI_CONTRACT = (
    ('delivery', 'adoption', 'audit'),
    ('delivery', 'adoption', 'scaffold'),
    ('validate-overlay',),
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
        if _ACTIVE_TRANSCRIPT is not None:
            _ACTIVE_TRANSCRIPT.append(msg+'\n')
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
             live: bool = False, target_root: Path | None = None,
             tty_live: bool = False) -> str:
    """Bounded native subprocess. Never strip useful exceptions or synthesize CI output."""
    env = safe_env(devtool_root=devtool_root, target_root=target_root or cwd, color_output=live)
    if live:
        # Native stdout is attached to the operator terminal; cannot forge
        # `gh watch` output. Do not print argv, which might contain secrets.
        say('LAUNCHER_NATIVE_BEGIN stage='+stage)
        try:
            # Only CI/watch uses inherited real terminal file descriptors.
            # A child behind stdout/stderr pipes is not a TTY and cannot
            # enable Devtool's interactive Rich monitor. Never forge one.
            real_tty = (tty_live and sys.stdin.isatty() and sys.stdout.isatty()
                        and sys.stderr.isatty() and 'NO_COLOR' not in env
                        and env.get('CLICOLOR') != '0' and env.get('TERM') != 'dumb'
                        and str(env.get('CI','')).lower() not in ('1','true','yes','on'))
            if real_tty:
                if _ACTIVE_TRANSCRIPT is not None:
                    _ACTIVE_TRANSCRIPT.append('LAUNCHER_V32_TTY_NATIVE_MONITOR: child output remains on the true terminal; native CI receipts own the details\n')
                    # Do not claim a complete launcher failure transcript when
                    # native child output bypasses the launcher's relay.
                    _ACTIVE_TRANSCRIPT.failed = True
                rc = subprocess.run(argv,cwd=cwd,env=env,timeout=timeout,check=False).returncode
            elif _ACTIVE_TRANSCRIPT is not None:
                rc = _stream_native_and_log(argv,cwd=cwd,env=env,timeout=timeout)
            else:
                rc = subprocess.run(argv,cwd=cwd,env=env,timeout=timeout,check=False).returncode
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Refused(f'COMMAND_UNAVAILABLE_OR_TIMED_OUT stage={stage} cause={type(e).__name__}') from e
        if rc:
            raise Refused(f'NATIVE_COMMAND_FAILED stage={stage} rc={rc} (native output above)')
        say('LAUNCHER_NATIVE_END stage='+stage+' rc=0')
        return ''
    try:
        cp = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                            text=True, errors='replace', timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Refused(f'COMMAND_UNAVAILABLE_OR_TIMED_OUT stage={stage} cause={type(e).__name__}') from e
    if _ACTIVE_TRANSCRIPT is not None:
        _ACTIVE_TRANSCRIPT.append('LAUNCHER_NATIVE_CAPTURE stage='+stage+' stdout:\n')
        _ACTIVE_TRANSCRIPT.append(cp.stdout)
        _ACTIVE_TRANSCRIPT.append('\nLAUNCHER_NATIVE_CAPTURE stage='+stage+' stderr:\n')
        _ACTIVE_TRANSCRIPT.append(cp.stderr)
        _ACTIVE_TRANSCRIPT.append('\n')
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
    if identity.get('branch') == base:
        raise Refused('DELIVERY_BASE_BRANCH_REFUSED branch='+base+
                      '; build the package from a clean named feature worktree; no changes made')
    if not 1<=len(pr_title)<=160 or len(pr_body)>8000:
        raise Refused('PR_METADATA_INVALID')
    devtool_contract={'repository':devtool['repository'],'schema_blob':schema_blob,
                      'source_sha':devtool['source_sha'],'source_tree':devtool['source_tree'],
                      'module_blob':devtool['module_blob']}
    # No implicit repin: keep exact build-runtime identity in every package.
    # Version-based execution is opt-in only for new explicit ABI manifests.
    if devtool.get('launcher_version') is not None:
        version=str(devtool['launcher_version'])
        from .launcher_runtime_compat import parse_version
        major=parse_version(version)[0]
        if major != 3:raise Refused('LAUNCHER_RUNTIME_UNSUPPORTED_MAJOR')
        devtool_contract['runtime_compatibility']={'minimum':version,'major':major}
    return {'schema':SCHEMA,'name':name,'source':{k:identity[k] for k in ('repository','branch','source_sha','source_tree')},
            'devtool':devtool_contract,
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
    if 'runtime_compatibility' in devtool:
        from .launcher_runtime_compat import parse_version
        contract=devtool['runtime_compatibility']
        if not isinstance(contract,dict) or set(contract)!={'minimum','major'}:
            raise Refused('LAUNCHER_COMPATIBILITY_MANIFEST_INVALID')
        version=parse_version(contract['minimum'])
        if contract['major'] != 3 or version[0] != contract['major']:
            raise Refused('LAUNCHER_COMPATIBILITY_MAJOR_INVALID')
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
    try:
        verify_metadata_manifest(plan, m.get('artifact_metadata', []))
    except ValidationContractError as exc:
        raise Refused('IMMUTABLE_VALIDATION_CONTRACT_INVALID ' + str(exc)) from exc
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
        if sum(i.file_size for i in infos)>MAX_TOTAL+MAX_TOTAL_HOOK_BYTES+300000:
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
        expected |= {'payload/'+a['path'] for a in m.get('artifact_metadata', [])}
        actual={n for n in names if n.startswith('payload/')}
        if expected != actual:raise Refused('ZIP_INVENTORY_MISMATCH')
        data={n:z.read(n) for n in expected}
        for c in m['inventory']:
            if digest(data['payload/'+c['path']]) != c['payload_sha256']:
                raise Refused('ZIP_PAYLOAD_HASH_MISMATCH '+c['path'])
        for item in m.get('artifact_metadata', []):
            raw = data['payload/'+item['path']]
            if len(raw) != item['size'] or digest(raw) != item['sha256']:
                raise Refused('ZIP_HOOK_DIGEST_MISMATCH '+item['path'])
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
    if target['branch']==args.base:
        raise Refused('DELIVERY_BASE_BRANCH_REFUSED branch='+args.base+
                      '; build from a clean named feature worktree, not the PR base')
    # The Devtool builder runtime is pinned by SHA/tree/blobs, not by a branch.
    # A detached, clean exact-pinned worktree is a valid build runtime.
    devtool=repo_identity(devtool_repo,clean=True,branch_required=False)
    if devtool['repository']!='Mikeyphw/devtool':raise Refused('WRONG_DEVTOOL_REPOSITORY')
    devtool['module_blob']=validate_sha(git(devtool_repo,'rev-parse','HEAD:devtool/launcher_contract_v3.py'), 'devtool.module_blob')
    devtool['launcher_version']=LAUNCHER_CONTRACT_VERSION
    schema_blob=validate_sha(git(devtool_repo,'rev-parse','HEAD:devtool/core_cli_schema.json'), 'schema_blob')
    if not (devtool_repo/'devtool/core_cli_schema.json').is_file():raise Refused('DEVTOOL_CLI_SCHEMA_MISSING')
    with Path(args.plan).open('r',encoding='utf-8') as f:plan=json.load(f)
    inv,payload=payload_contract(plan,Path(args.payload),source_repo)
    manifest=make_manifest(args.name,target,devtool,inv,args.commit_message,
                           args.pr_title,args.pr_body,args.base,schema_blob)
    try:
        extension, hooks = inspect_delivery_plan(plan, Path(args.payload))
    except ValidationContractError as exc:
        raise Refused('BUILDER_VALIDATION_CONTRACT_REFUSED ' + str(exc)) from exc
    if extension:
        manifest['plan'].update(extension)
        manifest['artifact_metadata'] = metadata_descriptors(hooks)
    else:
        if not bool(getattr(args, 'allow_legacy_static', False)):
            raise Refused('NEW_PACKAGE_REQUIRES_EXECUTABLE_VALIDATION; '
                'declare delivery_validation_mode=full, or explicitly acknowledge '
                'a legacy static-only package using --allow-legacy-static')
        say('LAUNCHER_V32_WARNING EXPLICIT_LEGACY_STATIC_ONLY; '
            'no executable overlay validation promised')
    verify_manifest(manifest)
    files={'__main__.py':bootstrap_content(devtool).encode('utf-8'),
           'manifest.json':(json.dumps(manifest,sort_keys=True,indent=2,ensure_ascii=False)+'\n').encode(),
           **payload, **{'payload/'+p:b for p,b in hooks.items()}}
    raw=zip_bytes(files)
    # Test the generated bytes before publication.
    with tempfile.TemporaryDirectory() as t:
        tmp=Path(t)/'tmp.pyz';tmp.write_bytes(raw)
        found,data=read_embedded_package(tmp)
        expected_data = {**payload, **{'payload/'+p:b for p,b in hooks.items()}}
        if found != manifest or data != expected_data:
            raise Refused('BUILT_PACKAGE_ROUNDTRIP_MISMATCH')
    output=Path(args.output)
    if output.suffix!='.pyz' or output.resolve().is_relative_to(source_repo.resolve()):
        raise Refused('OUTPUT_MUST_BE_PYZ_OUTSIDE_TARGET_REPO')
    exclusive_write(output,raw)
    say('LAUNCHER_V3_PACKAGE_PASS',str(output.resolve()),'sha256='+digest(raw))
    say('LAUNCHER_V3_PIN',manifest['source']['source_sha'],manifest['source']['source_tree'])
    say('LAUNCHER_V3_NO_GITHUB_MUTATION; next: cd TARGET && python3 -B package.pyz apply --approve-remote')


def require_feature_delivery_branch(m:dict[str,Any])->None:
    """Prevent native apply/commit on a PR base before any target mutation.

    The package pins a named target branch. A legacy package whose source is
    the PR base can still be inspected and its durable session investigated,
    but never newly applied or pushed; it must not silently change its pinned
    source branch. Branch creation is an explicit operator preparation step.
    """
    branch=m['source']['branch']
    base=m['delivery']['base']
    if branch==base:
        raise Refused('DELIVERY_BASE_BRANCH_REFUSED branch='+branch+
                      ' base='+base+'; no overlay applied or committed; '+
                      'create a clean named feature branch and rebuild an exact-pinned package')


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
        # A clean detached checkout is a trusted fetch/worktree *source*, but
        # never eligible for a named-branch fast-forward. Continue proving
        # origin, root, SHA, tree and cleanliness without requiring HEAD ref.
        try:
            old=repo_identity(requested,clean=True,branch_required=False)
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
    if 'runtime_compatibility' in pinned_dev:
        return repo, resolve_compatible_runtime(repo,devtool_root,pinned_dev)
    # On generated v3 packages the original runtime can be a feature branch;
    # the exact SHA+tree is still the authority for selection.
    if pinned_dev.get('source_sha') is None or pinned_dev.get('source_tree') is None:
        raise Refused('DEVTOOL_RUNTIME_PINS_REQUIRED')
    bound=resolve_runtime(repo,devtool_root,pinned_dev,allow_ff=allow_ff)
    return repo,bound


def runtime_probe(repo:Path,devtool_root:Path,m:dict[str,Any])->None:
    # The *target* must retain a named branch. An independently pinned native
    # Devtool runtime may have detached HEAD, as created by resolve_runtime().
    dev=repo_identity(devtool_root,clean=False,branch_required=False)
    # Native LIFE receipts are retained, not deleted or silently ignored.
    verify_postcommit_status(devtool_root)
    if dev['repository'] != 'Mikeyphw/devtool':raise Refused('DEVTOOL_ORIGIN_MISMATCH')
    if 'runtime_compatibility' in m['devtool']:
        runtime=check_compatible_runtime(devtool_root,m['devtool'])
        # Fail closed on dropped/renamed required native CLI commands
        # even if the newer module claims a compatible 3.x version.
        from .core_parser import parser_identity_projection
        present=parser_identity_projection()['commands']
        required=set(CLI_CONTRACT)
        missing=sorted(required-present)
        if missing:raise Refused('COMPATIBLE_RUNTIME_MISSING_NATIVE_CLI '+str(missing[:4]))
        code='import devtool; from pathlib import Path; print(Path(devtool.__file__).resolve())'
        actual=exec_cmd([sys.executable,'-B','-c',code],stage='runtime-source-import',
                    cwd=devtool_root,devtool_root=devtool_root,target_root=repo,timeout=30).strip()
        expected=str((devtool_root/'devtool/__init__.py').resolve())
        if actual!=expected:raise Refused('WRONG_PYTHON_DEVTOOL_MODULE expected='+expected+' actual='+actual)
        for command in (('delivery','adoption','scaffold'),('validate-overlay',)):
            native(repo,devtool_root,*command,'--help',stage='cli-help-'+'-'.join(command),timeout=45)
        say('LAUNCHER_V3_RUNTIME_COMPAT_PASS version='+runtime['version']+
            ' expected=>='+m['devtool']['runtime_compatibility']['minimum']+
            ' major=3.x head='+runtime['sha']+
            ' tree='+runtime['tree']+' module='+runtime['module']+
            ' schema='+runtime['schema']+' native_probes=2')
        return
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
    for command in (('delivery','adoption','scaffold'),('validate-overlay',)):
        native(repo,devtool_root,*command,'--help',stage='cli-help-'+'-'.join(command),timeout=45)
    say('LAUNCHER_V3_DEVTOOL_BOUND module='+actual+' schema_pinned=yes native_probes=2')


def native(repo:Path,devtool_root:Path,*parts:str,stage:str,timeout:float=120,
           live:bool=False,tty_live:bool=False)->str:
    # The supported native global flag prevents both the post-run copy prompt
    # and automatic copying. Only Devtool CLI children receive this option;
    # never replace internal overlay file transactions with clipboard controls.
    return exec_cmd([sys.executable,'-B','-m','devtool','--host','--repo',str(repo),
                     '--no-clipboard',*parts],
                    stage=stage,cwd=devtool_root,devtool_root=devtool_root,target_root=repo,
                    timeout=timeout,live=live,tty_live=tty_live)


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
    # New validation-aware packages must verify the native scaffold retained
    # every declared hook and exact validation contract before apply.
    # Legacy S7 plans have no extended metadata: preserve the original API
    # and its independently validated native validate-overlay operation.
    extended = bool(set(m.get('plan', {})) - {'schema_version', 'changes'}) or bool(
        m.get('artifact_metadata'))
    if extended:
        try:
            with zipfile.ZipFile(archive) as produced:
                artifact_manifest = json.loads(produced.read('.devtool-artifact.json'))
                for field in ('validation','preflight','finalize'):
                    if field in m['plan'] and artifact_manifest.get(field) != m['plan'][field]:
                        raise Refused('NATIVE_SCAFFOLD_DROPPED_CONTRACT '+field)
                reason = m['plan'].get('delivery_static_justification')
                if reason is not None and artifact_manifest.get('notes') != [
                        'Explicit static-only validation: ' + reason]:
                    raise Refused('NATIVE_SCAFFOLD_DROPPED_STATIC_JUSTIFICATION')
                for item in m.get('artifact_metadata', []):
                    content = produced.read(item['path'])
                    if len(content) != item['size'] or digest(content) != item['sha256']:
                        raise Refused('NATIVE_SCAFFOLD_HOOK_DRIFT '+item['path'])
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            raise Refused('NATIVE_SCAFFOLD_METADATA_INVALID') from exc
    native(repo,devtool_root,'validate-overlay',str(archive),stage='native-validate-overlay',timeout=330,live=True)
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
    require_feature_delivery_branch(m)
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


def ci_watch_timeout_seconds(checks:int,interval:int)->int:
    """Bound launcher supervision without preempting native CI polling.

    Native CI's registrar uses exponential backoff capped at 120 s, rather
    than a fixed interval. Reserve 120 s of network/diagnostics per poll,
    120 s of capped backoff per poll, and 180 s for final receipts. Native
    Devtool remains the only owner of CI outcome and poll-count limits.
    """
    if checks < 1 or checks > 60 or interval < 1 or interval > 120:
        raise Refused('INVALID_BOUNDED_CI_POLLING')
    return max(900, checks * (120 + 120) + 180)


def observe_delivery_ci(repo:Path,devtool_root:Path,sid:str,*,checks:int=25,interval:int=8)->int:
    if checks<1 or checks>60 or interval<1 or interval>120:
        raise Refused('INVALID_BOUNDED_CI_POLLING')
    native(repo,devtool_root,'delivery','session','ci','--id',sid,'--checks',str(checks),
           '--interval',str(interval),stage='native-source-bound-ci',
           timeout=ci_watch_timeout_seconds(checks,interval),live=True,tty_live=True)
    report=native_json(repo,devtool_root,'delivery','session','report','--id',sid,stage='native-ci-machine-receipt')
    ci=(report.get('receipt') or {}).get('ci_state') or (report.get('ci') or {}).get('state')
    say('LAUNCHER_V3_CI_STATE '+str(ci or 'UNKNOWN')+'; formal release NOT qualified')
    native(repo,devtool_root,'delivery','session','report','--id',sid,
           stage='native-saved-report',timeout=90,live=True)
    return 0 if ci in ('REQUIRED_CHECKS_PASSED','required_checks_passed') else 3


def _missing_committed_ci_policy(repo: Path) -> bool:
    """Only a genuinely absent policy is optional; never excuse dirty files.

    A tracked-but-deleted file, untracked policy, symlink or mutated committed
    policy must still reach Devtool's strict committed-policy validation.
    """
    relative='config/delivery-required-ci.json'
    stored=git(repo,'ls-tree','--name-only','HEAD','--',relative,
               stage='native-ci-policy-source-tree')
    if stored not in ('',relative):
        raise Refused('CI_POLICY_SOURCE_TREE_AMBIGUOUS')
    policy_file=repo/relative
    if (repo/'config').is_symlink():
        raise Refused('CI_POLICY_PARENT_SYMLINK_REFUSED')
    return not stored and not policy_file.exists() and not policy_file.is_symlink()


def required_ci_preflight(repo:Path,devtool_root:Path,m:dict[str,Any])->dict[str,Any] | None:
    """Use Devtool's *own* committed policy authority before any mutation.

    This does not infer workflows from observed CI runs, inject a policy,
    approve a release, or interpret the policy as a substitute for hosted CI.
    When *absent from both the source HEAD and worktree*, the policy is
    optional: report a warning and skip formal CI observation. This never
    qualifies hosted CI or a release. Existing malformed, uncommitted, dirty,
    symlinked or mismatched policies still refuse through Devtool's own strict
    policy authority; no existing security gate is bypassed.
    """
    if _missing_committed_ci_policy(repo):
        say('LAUNCHER_V3_WARNING REQUIRED_CI_POLICY_ABSENT '
            'path=config/delivery-required-ci.json; native required-CI watching '
            'will be skipped; hosted CI NOT QUALIFIED; delivery may continue')
        return None
    code=(
        'import json,sys; from pathlib import Path; '
        'from devtool.delivery_ci_policy import load_policy; '
        'p=load_policy(Path(sys.argv[1]),expected_repository=sys.argv[2],'
        'expected_base=sys.argv[3]); '
        'print(json.dumps(p.receipt(),sort_keys=True))'
    )
    output=exec_cmd([sys.executable,'-B','-c',code,str(repo),
                     m['source']['repository'],m['delivery']['base']],
                    stage='native-required-ci-preflight',cwd=devtool_root,
                    devtool_root=devtool_root,target_root=repo,timeout=40)
    try:
        receipt=json.loads(output)
    except ValueError as exc:
        raise Refused('NATIVE_REQUIRED_CI_PREFLIGHT_INVALID_JSON') from exc
    rows=receipt.get('required_workflows') if isinstance(receipt,dict) else None
    if (not isinstance(rows,list) or not rows or
            not isinstance(receipt.get('policy_sha256'),str) or
            not re.fullmatch(r'[0-9a-f]{64}',receipt['policy_sha256']) or
            not all(isinstance(row,dict) and isinstance(row.get('name'),str)
                        and row['name'] and type(row.get('workflow_id')) is int
                        and row['workflow_id']>0 for row in rows)):
        raise Refused('NATIVE_REQUIRED_CI_PREFLIGHT_RECEIPT_UNVERIFIED')
    say('LAUNCHER_V3_REQUIRED_CI_PREFLIGHT_PASS policy='+
        receipt['policy_sha256'][:12]+' workflows='+str(len(rows)))
    return receipt


def verify_postcommit_status(repo:Path)->None:
    """Preserve native LIFE cleanup receipts; never delete operator evidence.

    Current LIFE cleanup receipts use XDG state and do not dirty the target.
    Retain the historical worktree-receipt exception only for recovery of an
    already-started native transaction; never delete a user-owned receipt or
    waive unrelated worktree dirt.
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
    require_feature_delivery_branch(m)
    check_source(repo,m);runtime_probe(repo,devtool_root,m)
    ci_policy=required_ci_preflight(repo,devtool_root,m) if watch_ci else None
    archive=scaffold(repo,devtool_root,m,payload)
    # Full mode delegates executable tests/tasks/scripts to the existing
    # native Devtool validator and transaction. Static is explicitly bounded
    # and cannot silently skip declared validation.tests/tasks.
    mode = validation_execution_mode(m['plan'])
    # Required full-mode artifact validation is enabled by the native CLI itself.
    # Do not explicitly pass --validate: that would override the manifest's
    # restore/build/test booleans and run unrelated project validation.
    native_flags = () if mode == 'full' else ('--static-validate',)
    say('LAUNCHER_V32_VALIDATION_MODE '+mode+' native='+
        ('artifact-policy' if mode == 'full' else '--static-validate'))
    if mode == 'static' and m['plan'].get('delivery_static_justification'):
        say('LAUNCHER_V32_STATIC_JUSTIFICATION '+redact(m['plan']['delivery_static_justification']))
    native(repo,devtool_root,'apply','overlay',str(archive),'--yes','--commit',
           m['delivery']['commit_message'],'--commit-mode','new',*native_flags,
           '--keep-artifact',stage='native-atomic-apply-and-commit',timeout=1800,live=True)
    after=repo_identity(repo,clean=False)
    verify_postcommit_status(repo)
    # A newly added required-CI policy cannot be checked before its
    # source-bound native commit. Once committed, verify it through
    # native Devtool before starting CI observation. Existing malformed
    # policies still fail preflight prior to any mutation.
    if watch_ci and ci_policy is None and any(
            c['path']=='config/delivery-required-ci.json' and c['action']=='add'
            for c in m['inventory']):
        ci_policy=required_ci_preflight(repo,devtool_root,m)
        if ci_policy is None:
            raise Refused('POST_COMMIT_REQUIRED_CI_POLICY_NOT_VERIFIED')
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
            if ci_policy is None:
                say('LAUNCHER_V3_WARNING REQUIRED_CI_SKIPPED_NO_POLICY '
                    'session='+sid+'; PR delivery succeeded; hosted CI NOT QUALIFIED; '
                    'inspect GitHub checks separately')
                return 0  # Remote delivery succeeded; never assert CI passed.
            return observe_delivery_ci(repo,devtool_root,sid,checks=checks,interval=interval)
    else:say('LAUNCHER_V3_LOCAL_ONLY; no push or PR was attempted')
    return 0


def runtime_command(args:argparse.Namespace,package:Path)->int:
    m,payload=read_embedded_package(package)
    op=args.command
    if op=='apply' and getattr(args,'watch_ci',False) and not getattr(args,'approve_remote',False):
        raise Refused('WATCH_CI_REQUIRES_APPROVE_REMOTE')
    if op in ('check','apply'):
        # A legacy base-branch package must refuse before auto-fetch, scaffold,
        # native atomic apply, commit or session creation. Inspect/recovery stay available.
        require_feature_delivery_branch(m)
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
            require_feature_delivery_branch(m)
            if not args.approve_remote:raise Refused('REMOTE_DELIVERY_REQUIRES_EXPLICIT_APPROVAL')
            push_pr(repo,devtool_root,m,sid);return 0
        if required_ci_preflight(repo,devtool_root,m) is None:
            say('LAUNCHER_V3_WARNING REQUIRED_CI_SKIPPED_NO_POLICY '
                'session='+sid+'; hosted CI NOT QUALIFIED; '
                'inspect GitHub checks separately')
            return 0
        return observe_delivery_ci(repo,devtool_root,sid,checks=args.checks,interval=args.interval)
    raise Refused('UNKNOWN_RUNTIME_OPERATION')


def check_compatible_runtime(root:Path, definition:dict[str,Any])->dict[str,str]:
    """Attest the chosen Git *commit* and files, then verify declared 3.x ABI.

    Exact original devtool SHA/tree/blob remain in the immutable manifest as
    provenance, but need not equal this separately re-attested runtime.
    """
    identity=repo_identity(root,clean=False,branch_required=False)
    if identity['repository']!='Mikeyphw/devtool':
        raise Refused('COMPATIBLE_RUNTIME_WRONG_ORIGIN')
    verify_postcommit_status(root)
    module='devtool/launcher_contract_v3.py'
    schema='devtool/core_cli_schema.json'
    blobs={}
    for path in (module,schema):
        committed=validate_sha(git(root,'rev-parse','HEAD:'+path),path)
        if committed!=git(root,'hash-object',path):
            raise Refused('COMPATIBLE_RUNTIME_UNCOMMITTED_SOURCE '+path)
        blobs[path]=committed
    requirement=definition.get('runtime_compatibility')
    try:
        version=declared_version(root/module)
        require_version(version,requirement)
    except IncompatibleLauncherRuntime as exc:
        raise Refused(str(exc)) from exc
    # Package ABI != project release version. A major-incompatible module
    # must not disguise itself behind a 3.x version marker.
    import ast as _ast
    tree=_ast.parse((root/module).read_text(encoding='utf-8'))
    schema_markers=[n.value for n in tree.body if isinstance(n,_ast.Assign)
        and any(isinstance(t,_ast.Name) and t.id=='SCHEMA' for t in n.targets)]
    if len(schema_markers)!=1 or _ast.literal_eval(schema_markers[0])!='devtool.launcher-contract.v3':
        raise Refused('COMPATIBLE_RUNTIME_LAUNCHER_ABI_SCHEMA_MISMATCH')
    return {'version':version,'sha':identity['source_sha'],'tree':identity['source_tree'],
            'module':blobs[module],'schema':blobs[schema]}


def resolve_compatible_runtime(target:Path,requested:Path,definition:dict[str,Any])->Path:
    """Use verified requested runtime; freeze it if the target will mutate it.

    Never silently downgrade, repin, reset, fetch, or update a branch. New 3.x
    runtimes are selected by semantic ABI and independent Git provenance.
    """
    runtime=check_compatible_runtime(requested,definition)
    if requested.resolve()!=target.resolve():
        say('LAUNCHER_V3_SELECT_COMPATIBLE_RUNTIME '+runtime['version']+
            ' head='+runtime['sha']+' tree='+runtime['tree'])
        return requested
    root=_runtime_cache_root(target)
    dest=root/('compatible-runtime-'+runtime['sha'])
    if dest.exists() or dest.is_symlink():
        if dest.is_symlink() or not dest.is_dir():
            raise Refused('COMPATIBLE_RUNTIME_CACHE_INVALID')
        reused=check_compatible_runtime(dest,definition)
        if reused['sha']!=runtime['sha'] or reused['tree']!=runtime['tree']:
            raise Refused('COMPATIBLE_RUNTIME_CACHE_IDENTITY_DRIFT')
        say('LAUNCHER_V3_REUSE_COMPATIBLE_RUNTIME '+str(dest))
        return dest
    say('LAUNCHER_V3_FREEZE_COMPATIBLE_RUNTIME '+runtime['version']+
        ' head='+runtime['sha']+' worktree='+str(dest))
    exec_cmd(['git','-C',str(requested),'worktree','add','--detach',str(dest),runtime['sha']],
             stage='native-compatible-runtime-worktree',timeout=90,live=True)
    new=check_compatible_runtime(dest,definition)
    if new!=runtime:
        raise Refused('COMPATIBLE_RUNTIME_FREEZE_RESULT_MISMATCH')
    return dest


def compatible_bootstrap_content(devtool:dict[str,str])->str:
    """Minimal version-aware package entrypoint with no native engine logic."""
    from .launcher_runtime_compat import parse_version
    required=str(devtool['launcher_version'])
    major=parse_version(required)[0]
    if major!=3:raise Refused('COMPATIBLE_BOOTSTRAP_UNSUPPORTED_MAJOR')
    code = r'''# Source-attested 3.x native launcher bootstrap: NOT an overlay engine.
import ast, json, os, pathlib, re, subprocess, sys, zipfile
MINIMUM = _MINIMUM_
MAJOR = _MAJOR_
argv = sys.argv[1:]
if argv == ['inspect']:
    with zipfile.ZipFile(sys.argv[0]) as z:
        m=json.loads(z.read('manifest.json'))
    print(json.dumps({k:m[k] for k in ('schema','name','source','devtool','inventory','limitations')},indent=2,sort_keys=True))
    raise SystemExit(0)
if not zipfile.is_zipfile(sys.argv[0]):
    raise SystemExit('LAUNCHER_V3_REFUSED: corrupted immutable zipapp')
with zipfile.ZipFile(sys.argv[0]) as z:
    m=json.loads(z.read('manifest.json'))
required=m.get('devtool',{}).get('runtime_compatibility')
if required != {'minimum':MINIMUM,'major':MAJOR}:
    raise SystemExit('LAUNCHER_V3_REFUSED: immutable runtime ABI manifest mismatch')
env=dict(os.environ,GIT_NO_REPLACE_OBJECTS='1',GIT_TERMINAL_PROMPT='0',PYTHONDONTWRITEBYTECODE='1')
env.update(DEVTOOL_EXECUTION_PRESENTATION='plain',DEVTOOL_JOB_UI='plain',
           DEVTOOL_TEXTUAL_APPLY_HOST='0',DEVTOOL_TEXTUAL_EXECUTION_HOST='0')
if sys.stdout.isatty() and 'NO_COLOR' not in env and env.get('CLICOLOR')!='0' and env.get('TERM')!='dumb':
    env.setdefault('FORCE_COLOR','1')
GH=re.compile(r'(?:git@github[.]com:|https://github[.]com/|ssh://git@github[.]com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:[.]git)?/?\Z',re.I)
SHA=re.compile(r'[0-9a-f]{40}\Z')
VER=re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')
def git(path,*args):
    p=subprocess.run(['git','-C',str(path),*args],env=env,capture_output=True,
                     text=True,timeout=35,check=False)
    if p.returncode:raise RuntimeError('Cannot verify native Git operation '+args[0])
    return p.stdout.strip()
def trust(path):
    try:
        if path.is_symlink() or not path.is_dir():return None
        if pathlib.Path(git(path,'rev-parse','--show-toplevel')).resolve()!=path.resolve():return None
        match=GH.fullmatch(git(path,'remote','get-url','origin'))
        if match is None or match.group(1)!='Mikeyphw/devtool':return None
        flags=git(path,'status','--porcelain','--untracked-files=all')
        for line in flags.splitlines():
            if not re.fullmatch(r'\?\? \.devtool/private/cleanup/receipts/cleanup-receipt-[0-9a-f]{32}\.json',line):
                return None
            receipt=path/line[3:]
            if receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size>131072:return None
            if not str(json.loads(receipt.read_text(encoding='utf-8')).get('receipt_id','')).startswith('cleanup-receipt:'):
                return None
        sha=git(path,'rev-parse','HEAD')
        tree=git(path,'rev-parse','HEAD^{tree}')
        if not SHA.fullmatch(sha) or not SHA.fullmatch(tree):return None
        module=path/'devtool/launcher_contract_v3.py'
        schema=path/'devtool/core_cli_schema.json'
        for file in (module,schema):
            if file.is_symlink() or not file.is_file() or file.stat().st_size>8*1024*1024:return None
            rel=str(file.relative_to(path))
            commit=git(path,'rev-parse','HEAD:'+rel)
            if not SHA.fullmatch(commit) or git(path,'hash-object',rel)!=commit:return None
        source=module.read_text(encoding='utf-8')
        nodes=ast.parse(source).body
        markers=[x.value for x in nodes if isinstance(x,ast.Assign) and any(
                 isinstance(t,ast.Name) and t.id=='LAUNCHER_CONTRACT_VERSION' for t in x.targets)]
        if len(markers)!=1:return None
        sm=[x.value for x in nodes if isinstance(x,ast.Assign) and any(
            isinstance(t,ast.Name) and t.id=='SCHEMA' for t in x.targets)]
        if len(sm)!=1 or ast.literal_eval(sm[0])!='devtool.launcher-contract.v3':return None
        v=ast.literal_eval(markers[0])
        match=VER.fullmatch(v) if isinstance(v,str) else None
        if match is None:return None
        tup=tuple(int(i) for i in match.groups())
        low=tuple(int(i) for i in MINIMUM.split('.'))
        if tup[0]!=MAJOR or tup<low:return None
        return (v,sha,tree)
    except (OSError,ValueError,RuntimeError,SyntaxError,UnicodeError,subprocess.TimeoutExpired):
        return None
if '--repo' not in argv:
    try:
        target=pathlib.Path(git(pathlib.Path.cwd(),'rev-parse','--show-toplevel')).resolve()
        rm=GH.fullmatch(git(target,'remote','get-url','origin'))
        if rm is None or rm.group(1)!=m['source']['repository'] or git(target,'symbolic-ref','--quiet','--short','HEAD')!=m['source']['branch']:
            raise RuntimeError('wrong target')
    except (OSError,RuntimeError,KeyError,subprocess.TimeoutExpired):
        raise SystemExit('LAUNCHER_V3_REFUSED: cd into source-bound named target or pass --repo')
    argv.extend(['--repo',str(target)])
else:
    try:target=pathlib.Path(argv[argv.index('--repo')+1]).expanduser().absolute()
    except (IndexError,ValueError):raise SystemExit('LAUNCHER_V3_REFUSED: --repo needs a value')
explicit='--devtool-repo' in argv
if explicit:
    try:root=pathlib.Path(argv[argv.index('--devtool-repo')+1]).expanduser().absolute()
    except (IndexError,ValueError):raise SystemExit('LAUNCHER_V3_REFUSED: --devtool-repo needs a value')
    selected=trust(root)
    if selected is None:raise SystemExit('LAUNCHER_V3_REFUSED: explicit runtime is not verified compatible 3.x')
else:
    home=pathlib.Path.home()
    choices=[]
    if env.get('DEVTOOL_REPO'):choices.append(pathlib.Path(env['DEVTOOL_REPO']).expanduser())
    choices.extend((home/'Code/devtool',target,home/'Code/dev',home/'Code/devtool-launcher-v3'))
    for base in list(choices):
        if base.is_dir() and not base.is_symlink():
            try:
                for row in git(base,'worktree','list','--porcelain').splitlines():
                    if row.startswith('worktree '):choices.append(pathlib.Path(row[9:]))
            except (RuntimeError,OSError,subprocess.TimeoutExpired):pass
    root=None;selected=None;seen=set()
    for candidate in choices[:80]:
        candidate=candidate.absolute()
        if candidate in seen:continue
        seen.add(candidate)
        found=trust(candidate)
        if found is not None:
            root=candidate;selected=found;break
    if selected is None:
        raise SystemExit('LAUNCHER_V3_REFUSED: no trusted same-major 3.x runtime found; no repin or import')
    argv.extend(['--devtool-repo',str(root)])
# A runtime version is not evidence of source integrity: log actual SHA/tree.
print('LAUNCHER_V3_RUNTIME_VERSION_PASS version='+selected[0]+' major='+str(MAJOR)+
      '.x head='+selected[1]+' tree='+selected[2]+' original_build_sha='+m['devtool']['source_sha'],flush=True)
# Ensure installed Python source is from verified Git origin/commit before import.
for key in ('DEVTOOL_EXECUTION_PRESENTATION','DEVTOOL_JOB_UI','DEVTOOL_TEXTUAL_APPLY_HOST','DEVTOOL_TEXTUAL_EXECUTION_HOST'):
    os.environ[key]=env[key]
if 'FORCE_COLOR' in env and 'FORCE_COLOR' not in os.environ:os.environ['FORCE_COLOR']=env['FORCE_COLOR']
sys.argv[1:]=argv
sys.path.insert(0,str(root))
from devtool.launcher_contract_v3 import main
raise SystemExit(main())
'''
    code=code.replace('_MINIMUM_',repr(required)).replace('_MAJOR_',repr(major))
    compile(code,'compatible-bootstrap.py','exec')
    return code


def bootstrap_content(devtool:dict[str,str])->str:
    """Select verified same-major runtime for opt-in manifests; exact for legacy."""
    if 'launcher_version' in devtool:
        return compatible_bootstrap_content(devtool)
    if 'runtime_compatibility' in devtool:
        return compatible_bootstrap_content(
            {**devtool,'launcher_version':devtool['runtime_compatibility']['minimum']})
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
        q.add_argument('--allow-legacy-static',action='store_true',
            help='Explicit compatibility: legacy static-only plan without executable checks')
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
    return _with_failure_transcript(_main_impl)


def _main_impl()->int:
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
            if args.command in ('ci','apply') and (args.checks<1 or args.checks>60 or args.interval<1 or args.interval>120):
                raise Refused('INVALID_BOUNDED_CI_POLLING')
            return runtime_command(args,package)
        raise Refused('UNKNOWN_OPERATION')
    except (Refused,ValueError,OSError,KeyError,zipfile.BadZipFile,json.JSONDecodeError) as exc:
        say('LAUNCHER_V3_REFUSED '+redact(str(exc)))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
