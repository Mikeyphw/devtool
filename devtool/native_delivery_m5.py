"""M5: launcher-optional, source-bound native overlay delivery operator.

Normal .zip uses Devtool's native overlay validation/apply/continuity authority.
Preverified v3 .pyz delegates to the existing thin source-bound launcher.
Never establishes a second transaction, GitHub client, CI authority or UX host.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from typing import Any

_SHA40 = re.compile(r"[a-f0-9]{40}\Z")
_SID = re.compile(r"[a-f0-9]{32}\Z")
_AUTH = frozenset({'auto','app','env','gh'})

class NativeM5Refused(RuntimeError):
    pass


def _safe_root(value: str) -> Path:
    if not value:
        raise NativeM5Refused('M5_REPO_REQUIRED')
    p = Path(value).expanduser().absolute()
    if p.is_symlink() or not p.is_dir():
        raise NativeM5Refused('M5_REPO_NOT_REGULAR_DIRECTORY')
    return p.resolve()


def _artifact(value: str) -> Path:
    if not value:
        raise NativeM5Refused('M5_ARTIFACT_REQUIRED')
    path = Path(value).expanduser().absolute()
    if path.suffix.lower() not in ('.zip','.pyz') or path.is_symlink() or not path.is_file():
        raise NativeM5Refused('M5_ARTIFACT_MUST_BE_REGULAR_ZIP_OR_PYZ')
    if path.stat().st_size > 80 * 1024 * 1024:
        raise NativeM5Refused('M5_ARTIFACT_TOO_LARGE')
    return path.resolve()


def _preverify(package: Path) -> str:
    if package.suffix == '.zip':
        # Native validate-overlay remains the authority for the full archive.
        # Merely locating a regular ZIP never counts as validation.
        return 'native-zip'
    import zipfile
    if not zipfile.is_zipfile(package):
        raise NativeM5Refused('M5_PYZ_SOURCE_AND_INTEGRITY_UNVERIFIED')
    from .launcher_contract_v3 import read_embedded_package
    try:
        manifest, _ = read_embedded_package(package)
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        raise NativeM5Refused('M5_PYZ_SOURCE_AND_INTEGRITY_UNVERIFIED: '+type(exc).__name__) from None
    if not isinstance(manifest, dict) or manifest.get('schema') != 'devtool.launcher-contract.v3':
        raise NativeM5Refused('M5_PYZ_V3_MANIFEST_REQUIRED; legacy inspect only')
    return 'verified-pyz-v3'


def _inspection(package: Path) -> dict[str, Any]:
    import hashlib
    import zipfile
    if not zipfile.is_zipfile(package):
        raise NativeM5Refused('M5_ARCHIVE_NOT_VALID_ZIP_CONTAINER')
    receipt = {'artifact_sha256': hashlib.sha256(package.read_bytes()).hexdigest(),
               'kind':'zip' if package.suffix == '.zip' else 'pyz',
               'artifact':str(package), 'read_only':True,
               'authority':'native-validate-overlay' if package.suffix=='.zip' else 'v3-verified-bootstrap'}
    if package.suffix == '.pyz':
        try:
            verified=_preverify(package)
            receipt['verification']=verified
        except NativeM5Refused:
            # No attempt to interpret or execute older immutable bootstrap.
            receipt['verification']='legacy-or-unsupported-read-only'
    else:
        receipt['verification']='requires-native-check'
    return receipt


def _require_zip_v3_commit_session(package: Path) -> None:
    """Reject unsafe ZIP apply *before* native validation or commit.

    The existing native M3 adapter emits a committed session only for a
    schema-v3 overlay with a validated delivery contract. Legacy schema-v2
    S7 scaffolds are still inspectable/checkable, but their apply cannot
    truthfully promise a post-commit M3 session.
    """
    import zipfile
    from .native_delivery_contract_v3 import normalize_delivery_contract

    if not zipfile.is_zipfile(package):
        raise NativeM5Refused('M5_ZIP_CONTAINER_INVALID')
    try:
        with zipfile.ZipFile(package) as archive:
            entries = [x for x in archive.infolist()
                       if x.filename == '.devtool-artifact.json']
            if len(entries) != 1 or not 0 < entries[0].file_size <= 128 * 1024:
                raise NativeM5Refused('M5_ZIP_UNIQUE_MANIFEST_REQUIRED')
            manifest = json.loads(archive.read(entries[0]))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        raise NativeM5Refused('M5_ZIP_MANIFEST_UNREADABLE') from None
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 3:
        raise NativeM5Refused('M5_ZIP_V3_REQUIRED_FOR_APPLY; legacy ZIP inspect/check only')
    try:
        contract = normalize_delivery_contract(manifest)
    except (ValueError, TypeError, KeyError):
        raise NativeM5Refused('M5_ZIP_V3_DELIVERY_CONTRACT_INVALID') from None
    if not isinstance(contract, dict) or contract.get('schema') != 'devtool.native-delivery-contract.v1':
        raise NativeM5Refused('M5_ZIP_V3_CONTINUITY_UNPROVEN')


def _attached_terminal() -> bool:
    """Use the operator's existing terminal; no tmux host or fake TTY."""
    return all(getattr(stream, 'isatty', lambda: False)()
               for stream in (sys.stdin, sys.stdout, sys.stderr))


def _child_env(*, attached: bool | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    # No separate full-screen Textual UI; preserve native Rich/ANSI otherwise.
    env['DEVTOOL_TEXTUAL_APPLY_HOST'] = '0'
    env['DEVTOOL_TEXTUAL_EXECUTION_HOST'] = '0'
    if attached is None:
        attached = _attached_terminal()
    if not attached:
        env['DEVTOOL_EXECUTION_PRESENTATION'] = 'plain'
        env['DEVTOOL_JOB_UI'] = 'plain'
        env['DEVTOOL_RICH_LIVE'] = '0'
        env['GIT_TERMINAL_PROMPT'] = '0'
    return env


def _native_child_cwd(argv: list[str], repo: Path) -> Path:
    """Do not shadow the independently selected M2 runtime with the target.

    Native M5 controls target with an explicit `--repo`, but Python `-m`
    searches `cwd` first. Starting the nested controller inside the target
    makes M2 correctly refuse runtime==target. The runtime package containing
    this function is the controller; only nested Devtool CLI commands move to
    its root. Verified launcher PYZ and arbitrary native subprocesses keep
    their previous cwd contract. M2 still checks the actual runtime Git tree.
    """
    if (len(argv)>=4 and argv[0]==sys.executable and
            argv[1:4]==['-B','-m','devtool']):
        runtime=Path(__file__).resolve().parent.parent
        if runtime==repo.resolve():
            raise NativeM5Refused('M5_SEPARATE_RUNTIME_REQUIRED')
        return runtime
    return repo


def _run(argv: list[str], *, repo: Path, timeout: int = 1800) -> tuple[int, str]:
    """Foreground child: actual native TTY when attached; bounded plain fallback.

    The returned text is only machine-mode session output, NEVER evidence
    for native success. Attached mode resolves committed sessions via native
    history and integrity-checked journals, not by scraping Rich output.
    """
    attached = _attached_terminal()
    print('M5_NATIVE_FOREGROUND '+ ' '.join(argv[:4]) +
          ' tty=' + ('attached' if attached else 'plain-fallback'),flush=True)
    if attached:
        with subprocess.Popen(argv, cwd=str(_native_child_cwd(argv,repo)), env=_child_env(attached=True)) as p:
            print('M5_CHILD_PID '+str(p.pid), flush=True)
            try:
                rc = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.terminate()
                try: p.wait(timeout=10)
                except subprocess.TimeoutExpired: p.kill(); p.wait()
                raise NativeM5Refused('M5_NATIVE_TIMEOUT; inspect durable history; NEVER REAPPLY') from None
            except KeyboardInterrupt:
                # The foreground process group may have received Ctrl-C too.
                # Do not leave an unreaped child or start a second native job.
                try: p.wait(timeout=10)
                except subprocess.TimeoutExpired: p.terminate(); p.wait()
                raise
        return rc, ''

    # Nonterminal automation: do not force ANSI, report actual child streams.
    outputs: list[str] = []
    with subprocess.Popen(argv, cwd=str(_native_child_cwd(argv,repo)), env=_child_env(attached=False),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, bufsize=1) as p:
        def consume(stream, output, collect):
            for line in iter(stream.readline, ''):
                output.write(line); output.flush()
                if collect and len(line)<4096 and ('SESSION_ID' in line):
                    outputs.append(line)
        threads = [threading.Thread(target=consume,args=(p.stdout,sys.stdout,True),daemon=True),
                   threading.Thread(target=consume,args=(p.stderr,sys.stderr,False),daemon=True)]
        for thread in threads: thread.start()
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.terminate()
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired: p.kill(); p.wait()
            raise NativeM5Refused('M5_NATIVE_TIMEOUT; inspect durable history; NEVER REAPPLY') from None
        finally:
            # Descendant processes may retain an inherited pipe after the
            # direct child exits; never hang indefinitely during cleanup.
            for thread in threads: thread.join(timeout=3)
            if any(thread.is_alive() for thread in threads):
                for stream in (p.stdout, p.stderr):
                    if stream is not None: stream.close()
                raise NativeM5Refused('M5_NATIVE_OUTPUT_PIPE_NOT_CLOSED; reconcile durable state, NEVER REAPPLY')
    return rc, ''.join(outputs)


def _session_id(s: str) -> str:
    if not isinstance(s,str) or not _SID.fullmatch(s):
        raise NativeM5Refused('M5_SESSION_ID_INVALID')
    return s


def _bound_status(sid: str,repo: Path) -> dict[str,Any]:
    from .delivery_session import status
    value=status(_session_id(sid))
    if Path(str(value.get('repo_root') or '')).resolve()!=repo:
        raise NativeM5Refused('M5_SESSION_WORKTREE_MISMATCH')
    return value


def _summary(bound: dict[str,Any], *, verb: str, format: str = 'text') -> dict[str, Any]:
    """Stable, secret-free projection. Historical evidence is not called live qualification."""
    native=bound.get('native_apply') or {}
    pr=bound.get('pr') or {}
    ci=bound.get('ci') or {}
    source=bound.get('head_sha') or ''
    tests=native.get('tests') or bound.get('validation') or {}
    steps=[]
    if native.get('state')!='COMMITTED':steps.append('native_commit_not_attested')
    if not pr:steps.append('pr_not_qualified')
    if not ci or ci.get('state') not in ('REQUIRED_CHECKS_PASSED','PASSED'):
        steps.append('required_ci_needs_live_qualification')
    if not isinstance(source,str) or not _SHA40.fullmatch(source):
        steps.append('source_head_unverified')
    result={
        'schema':'devtool.native-m5-summary.v1','verb':verb,
        'repository':str(bound.get('repository') or ''),
        'branch':str(bound.get('head') or ''),
        'source_head':source if isinstance(source,str) and _SHA40.fullmatch(source) else '',
        'native_transaction':str(native.get('transaction_id') or ''),
        'native_history':str(native.get('history_id') or ''),
        'native_commit':str(native.get('commit') or ''),
        'native_validation':str(native.get('validation_status') or ''),
        'tests_recorded':bool(tests),
        'tests_evidence':{'requested':str(tests.get('requested') or 'not_recorded') if isinstance(tests,dict) else 'not_recorded',
                          'executed':str(tests.get('executed') or 'not_recorded') if isinstance(tests,dict) else 'not_recorded',
                          'skipped':str(tests.get('skipped') or 'not_recorded') if isinstance(tests,dict) else 'not_recorded'},
        'session_id':str(bound.get('id') or ''),
        'session_stage':str(bound.get('stage') or ''),
        'pr_url':str(pr.get('html_url') or pr.get('url') or '') if isinstance(pr,dict) else '',
        'pr_head':str(pr.get('head_sha') or '') if isinstance(pr,dict) else '',
        'ci_scope':'recorded_historical_not_live_qualified',
        'ci_state':str(ci.get('state') or '') if isinstance(ci,dict) else '',
        'unresolved':steps,
        'automatic_clipboard':False,'fullscreen_ui':False,
        'release_qualified':False,'device_qualified':False,
    }
    if format=='json':
        print(json.dumps(result,indent=2,sort_keys=True),flush=True)
    else:
        print('=== M5 native delivery summary ===',flush=True)
        for key in ('repository','branch','source_head','native_transaction','native_history',
                    'native_commit','native_validation','session_id','session_stage',
                    'pr_url','pr_head','ci_scope','ci_state'):
            print(f'{key:24} {result[key] or "NOT_RECORDED"}',flush=True)
        for stage,actual in result['tests_evidence'].items():
            print(f'tests_{stage:18} {actual}',flush=True)
        print('unresolved: '+(', '.join(steps) if steps else 'none_recorded; live qualification remains separate'),flush=True)
        print('release/device: NOT_PERFORMED',flush=True)
    return result


def _native_git(repo: Path, *args: str) -> str:
    """Read-only source identity; cannot mutate a native transaction."""
    env = _child_env()
    env['GIT_NO_REPLACE_OBJECTS'] = '1'
    try:
        done = subprocess.run(['git', '-C', str(repo), *args], env=env,
                              capture_output=True, text=True, timeout=30,
                              check=False)
    except (OSError, subprocess.SubprocessError):
        raise NativeM5Refused('M5_GIT_SOURCE_IDENTITY_UNAVAILABLE') from None
    if done.returncode:
        raise NativeM5Refused('M5_GIT_SOURCE_IDENTITY_UNAVAILABLE')
    return done.stdout.strip()


def _committed_zip_session(repo: Path, package: Path, before: str) -> str:
    """Resolve a committed ZIP via canonical history + integrity-checked journal.

    Called ONLY after the one native apply subprocess exited zero. The CLI's
    Rich terminal presentation may not emit NATIVE_M3_SESSION_ID verbatim;
    native history and journal are the authority, not terminal text.
    This NEVER writes a journal, adopts a session, or replays an overlay.
    """
    import hashlib
    import stat
    from .artifacts import load_history
    from .delivery_session import _state_root, status

    current = _native_git(repo, 'rev-parse', 'HEAD')
    if (not _SHA40.fullmatch(before) or not _SHA40.fullmatch(current)
            or current == before or _native_git(repo, 'rev-parse', 'HEAD^') != before
            or _native_git(repo, 'rev-parse', '--show-toplevel') != str(repo)
            or _native_git(repo, 'status', '--porcelain', '--untracked-files=all')):
        raise NativeM5Refused('M5_ZIP_POSTCOMMIT_SOURCE_UNVERIFIED; DO NOT REAPPLY')

    artifact = str(package.resolve())
    fingerprint = hashlib.sha256(package.read_bytes()).hexdigest()
    matching = [h for h in load_history(repo) if isinstance(h, dict)
                and h.get('commit_hash') == current and h.get('artifact') == artifact
                and h.get('sha256') == fingerprint]
    if len(matching) != 1:
        raise NativeM5Refused('M5_ZIP_NATIVE_HISTORY_NOT_UNIQUE; DO NOT REAPPLY')
    history = matching[0]
    native_tx = history.get('transaction') or {}
    native_manifest = history.get('manifest') or {}
    if (history.get('status') not in ('ok','applied','success','succeeded')
            or history.get('validation_status') not in ('ok','passed','success','succeeded')
            or native_tx.get('status') != 'completed' or not native_tx.get('id')
            or not history.get('history_id') or not isinstance(native_manifest, dict)
            or native_manifest.get('schema_version') != 3):
        raise NativeM5Refused('M5_ZIP_NATIVE_HISTORY_NOT_QUALIFIED; DO NOT REAPPLY')

    # Preserve discoverability after state-root upgrades. Always verify the
    # exact matching session; never infer success from terminal log text.
    from .xdg import state_roots
    folders = tuple(base / 'delivery' / 'sessions' for base in state_roots())
    seen = 0
    candidates: list[str] = []
    for folder in folders:
        if not folder.exists():
            continue
        if (folder.is_symlink() or not folder.is_dir()
                or folder.stat().st_mode & 0o077):
            raise NativeM5Refused('M5_ZIP_SESSION_STORE_UNSAFE; DO NOT REAPPLY')
        for entry in sorted(folder.iterdir()):
            seen += 1
            if seen > 4096:
                raise NativeM5Refused('M5_ZIP_SESSION_STORE_OVERSIZED; DO NOT REAPPLY')
            if not re.fullmatch(r'[a-f0-9]{32}\.json', entry.name):
                continue
            if entry.is_symlink() or not entry.is_file() or not stat.S_ISREG(entry.stat().st_mode):
                raise NativeM5Refused('M5_ZIP_SESSION_ENTRY_UNSAFE; DO NOT REAPPLY')
            if not 0 < entry.stat().st_size <= 262144:
                raise NativeM5Refused('M5_ZIP_SESSION_ENTRY_SIZE_INVALID; DO NOT REAPPLY')
            try:
                envelope = json.loads(entry.read_text(encoding='utf-8'))
            except (OSError, UnicodeError, ValueError):
                # Corrupt unrelated entries do not become evidence.
                continue
            if not isinstance(envelope, dict) or envelope.get('repo_root') != str(repo):
                continue
            if envelope.get('head_sha') != current:
                continue
            # status verifies the native journal checksum, Git binding, and event
            # stream. A corrupt *matching* record must refuse, never be skipped.
            try:
                bound = status(entry.stem)
            except (OSError, ValueError, RuntimeError):
                raise NativeM5Refused('M5_ZIP_MATCHING_SESSION_INVALID; DO NOT REAPPLY') from None
            receipt = bound.get('native_apply') or {}
            overlay = bound.get('overlay') or {}
            if (bound.get('id') != entry.stem or bound.get('repo_root') != str(repo)
                    or bound.get('head_sha') != current
                    or bound.get('source_binding') != 'matching'
                    or bound.get('resumable') is not True
                    or bound.get('stage') != 'PLANNED'
                    or receipt.get('state') != 'COMMITTED'
                    or receipt.get('commit') != current
                    or receipt.get('transaction_status') != 'completed'
                    or receipt.get('transaction_id') != native_tx['id']
                    or receipt.get('history_id') != history['history_id']
                    or receipt.get('artifact_sha256') != fingerprint
                    or overlay.get('path') != artifact or overlay.get('sha256') != fingerprint
                    or bound.get('intents') or bound.get('pr') or bound.get('ci')):
                raise NativeM5Refused('M5_ZIP_MATCHING_SESSION_EVIDENCE_DRIFT; DO NOT REAPPLY')
            candidates.append(bound['id'])

    if len(candidates) != 1:
        raise NativeM5Refused('M5_ZIP_COMMITTED_SESSION_NOT_UNIQUE; DO NOT REAPPLY')
    print('M5_ZIP_SESSION_DURABLY_VERIFIED ' + candidates[0], flush=True)
    return candidates[0]


def _committed_pyz_session(repo: Path, package: Path, before: str) -> str:
    """Read-only source + committed history + journal reconciliation for v3 pyz.

    Exact native SHA/tree plus payload inventory, transaction, history, and
    journal must all identify one result. Never auto-adopt or replay here.
    """
    import hashlib
    import stat
    from .launcher_contract_v3 import read_embedded_package
    from .artifacts import load_history
    from .delivery_session import _state_root, status
    from .xdg import state_roots
    manifest, _ = read_embedded_package(package)
    source = manifest.get('source') or {}
    after = _native_git(repo, 'rev-parse', 'HEAD')
    if (not _SHA40.fullmatch(before) or after == before or
            source.get('source_sha') != before or
            source.get('source_tree') != _native_git(repo,'rev-parse', before + '^{tree}') or
            _native_git(repo,'rev-parse','HEAD^') != before or
            _native_git(repo,'rev-parse','--show-toplevel') != str(repo) or
            _native_git(repo,'status','--porcelain','--untracked-files=all')):
        raise NativeM5Refused('M5_PYZ_COMMITTED_SOURCE_NOT_ATTESTED; DO NOT REAPPLY')
    inventory = manifest.get('inventory')
    if not isinstance(inventory, list) or not inventory:
        raise NativeM5Refused('M5_PYZ_PAYLOAD_INVENTORY_UNVERIFIED; DO NOT REAPPLY')
    for entry in inventory:
        if not isinstance(entry, dict):
            raise NativeM5Refused('M5_PYZ_INVENTORY_INVALID; DO NOT REAPPLY')
        path = entry.get('path')
        action = entry.get('action')
        if not isinstance(path,str) or not path or path.startswith('/') or '..' in path.split('/'):
            raise NativeM5Refused('M5_PYZ_INVENTORY_PATH_UNSAFE; DO NOT REAPPLY')
        target = repo / path
        if action == 'delete':
            if target.exists() or target.is_symlink():
                raise NativeM5Refused('M5_PYZ_POSTCOMMIT_DELETE_MISMATCH; DO NOT REAPPLY')
        elif action in ('add','modify'):
            expected = entry.get('payload_sha256')
            if (not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{64}',expected) or
                    target.is_symlink() or not target.is_file() or
                    hashlib.sha256(target.read_bytes()).hexdigest()!=expected):
                raise NativeM5Refused('M5_PYZ_POSTCOMMIT_CONTENT_MISMATCH; DO NOT REAPPLY')
        else:
            raise NativeM5Refused('M5_PYZ_INVENTORY_ACTION_UNSUPPORTED; DO NOT REAPPLY')
    histories=[h for h in load_history(repo) if isinstance(h, dict) and h.get('commit_hash')==after
               and h.get('status') in ('ok','applied','success','succeeded')
               and h.get('validation_status') in ('ok','passed','success','succeeded')
               and isinstance(h.get('manifest'),dict)
               and h['manifest'].get('schema_version')==3
               and (h.get('transaction') or {}).get('status')=='completed'
               and h.get('history_id')]
    if len(histories)!=1:
        raise NativeM5Refused('M5_PYZ_HISTORY_NOT_UNIQUE; DO NOT REAPPLY')
    history=histories[0]
    fingerprint=history.get('sha256')
    if not isinstance(fingerprint,str) or not re.fullmatch(r'[0-9a-f]{64}',fingerprint):
        raise NativeM5Refused('M5_PYZ_HISTORY_DIGEST_INVALID; DO NOT REAPPLY')
    # History's archive is not the top-level pyz; it is the native archive
    # scaffolded *by* the verified pyz. Do not confuse their SHA256s.
    candidates=[]
    for root in state_roots():
        folder=root/'delivery'/'sessions'
        if not folder.exists():continue
        if folder.is_symlink() or not folder.is_dir() or folder.stat().st_mode & 0o077:
            raise NativeM5Refused('M5_PYZ_SESSION_STORE_UNSAFE; DO NOT REAPPLY')
        for index,entry in enumerate(sorted(folder.iterdir())):
            if index>=4096:raise NativeM5Refused('M5_PYZ_SESSION_STORE_OVERSIZED')
            if not re.fullmatch(r'[0-9a-f]{32}\.json',entry.name):continue
            if entry.is_symlink() or not entry.is_file() or not stat.S_ISREG(entry.stat().st_mode):
                raise NativeM5Refused('M5_PYZ_SESSION_ENTRY_UNSAFE')
            if not 0<entry.stat().st_size<=262144:
                raise NativeM5Refused('M5_PYZ_SESSION_ENTRY_OVERSIZED')
            try: obj=json.loads(entry.read_text(encoding='utf-8'))
            except (OSError,UnicodeError,ValueError):continue
            if not isinstance(obj,dict) or obj.get('head_sha')!=after or obj.get('repo_root')!=str(repo):continue
            try: bound=status(entry.stem)
            except (OSError,ValueError,RuntimeError):
                raise NativeM5Refused('M5_PYZ_MATCHING_JOURNAL_INVALID; DO NOT REAPPLY') from None
            native=bound.get('native_apply') or {}
            overlay=bound.get('overlay') or {}
            if (bound.get('source_binding') != 'matching' or bound.get('resumable') is not True
                    or bound.get('stage') != 'PLANNED' or bound.get('intents') or bound.get('pr') or bound.get('ci')
                    or native.get('state')!='COMMITTED' or native.get('commit')!=after
                    or native.get('source_commit')!=before
                    or native.get('transaction_status')!='completed'
                    or native.get('transaction_id')!=(history.get('transaction') or {}).get('id')
                    or native.get('history_id')!=history['history_id']
                    or native.get('artifact_sha256')!=fingerprint
                    or overlay.get('sha256')!=fingerprint
                    or overlay.get('path')!=history.get('artifact')):
                raise NativeM5Refused('M5_PYZ_SESSION_EVIDENCE_DRIFT; DO NOT REAPPLY')
            candidates.append(entry.stem)
    if len(candidates)!=1:
        raise NativeM5Refused('M5_PYZ_COMMITTED_SESSION_NOT_UNIQUE; DO NOT REAPPLY')
    return candidates[0]


def _session_from_log(log: str, kind: str) -> str:
    pat=r'^LAUNCHER_V3_SESSION_ID\s+([0-9a-f]{32})$' if kind=='verified-pyz-v3' else r'^NATIVE_M3_SESSION_ID\s+([0-9a-f]{32})\b'
    ids=re.findall(pat,log,re.M)
    if len(set(ids))!=1 or not ids:
        raise NativeM5Refused('M5_COMMIT_OR_SESSION_UNCERTAIN; inspect native history, DO NOT REAPPLY')
    return ids[-1]


def main(args: argparse.Namespace) -> int:
    try:
        op=str(getattr(args,'native_command','') or '')
        repo=_safe_root(str(getattr(args,'repo','') or ''))
        if op in ('inspect','check','apply'):
            path=_artifact(str(getattr(args,'artifact','') or ''))
            if op=='inspect':
                data=_inspection(path)
                print(json.dumps(data,indent=2,sort_keys=True),flush=True)
                return 0
            kind=_preverify(path)
            # Prevent a legacy ZIP commit without an M3 session receipt.
            # Validate the complete native v3 contract before any child is run.
            if op=='apply' and kind=='native-zip':
                _require_zip_v3_commit_session(path)
            if kind=='verified-pyz-v3':
                runtime=_safe_root(str(getattr(args,'devtool_repo','') or repo))
                command=[sys.executable,'-B',str(path), 'check',
                         '--repo',str(repo),'--devtool-repo',str(runtime),'--no-auto-ff']
            else:
                command=[sys.executable,'-B','-m','devtool','--host','--no-clipboard',
                         '--repo',str(repo),'validate-overlay',str(path)]
            rc,_=_run(command,repo=repo,timeout=720)
            if rc:
                print('M5_NATIVE_CHECK_FAILED rc='+str(rc),file=sys.stderr,flush=True)
                return rc if rc>0 else 128-rc
            print('M5_NATIVE_CHECK_PASS kind='+kind,flush=True)
            if op=='check':return 0
            if not bool(getattr(args,'yes',False)):
                raise NativeM5Refused('M5_APPLY_APPROVAL_REQUIRED --yes')
            before_apply = _native_git(repo, 'rev-parse', 'HEAD')
            if kind=='verified-pyz-v3':
                apply_cmd=[sys.executable,'-B',str(path),'apply',
                           '--repo',str(repo),'--devtool-repo',str(runtime),'--no-auto-ff']
            else:
                # Commit is source-owned and immutable; the artifact's v3 manifest
                # declares validation and the native apply engine owns rollback.
                apply_cmd=[sys.executable,'-B','-m','devtool','--host','--no-clipboard',
                           '--repo',str(repo),'apply','overlay',str(path),'--yes','--commit',
                           'feat(native): adopt M5 native ZIP delivery','--commit-mode','new',
                           '--keep-artifact']
            rc,log=_run(apply_cmd,repo=repo,timeout=2100)
            if rc:
                print('M5_APPLY_FAILED rc='+str(rc)+'; inspect native transaction, NEVER REAPPLY',file=sys.stderr,flush=True)
                return rc if rc>0 else 128-rc
            # Native history is authoritative; Rich output may omit the
            # NATIVE_M3_SESSION_ID text even after a committed M3 adoption.
            sid=(_committed_zip_session(repo,path,before_apply) if kind=='native-zip'
                 else _committed_pyz_session(repo,path,before_apply))
            bound=_bound_status(sid,repo)
            _summary(bound,verb='apply',format='json' if getattr(args,'_devtool_json',False) else 'text')
            return 0
        sid=_session_id(str(getattr(args,'native_session_id','') or ''))
        bound=_bound_status(sid,repo)
        if op=='resume':
            from .delivery_session import resume
            result=resume(sid)
            if Path(str(result.get('repo_root') or '')).resolve()!=repo:
                raise NativeM5Refused('M5_RESUME_REPOSITORY_DRIFT')
            bound=_bound_status(sid,repo)
        elif op=='deliver':
            if not bool(getattr(args,'yes',False)):
                raise NativeM5Refused('M5_DELIVER_REQUIRES_EXPLICIT_YES')
            auth=str(getattr(args,'auth','auto') or 'auto')
            if auth not in _AUTH:raise NativeM5Refused('M5_AUTH_MODE_UNSUPPORTED')
            if auth=='app':
                # Controller capability check: a stale, independently pinned M2
                # runtime must never journal a workflow-writing push before
                # the modern M4 provider/guard code is in effect.
                from .github_app import _PERMISSIONS, _check_git_argv
                if _PERMISSIONS.get('branch_write_workflows') != {'contents':'write','workflows':'write'}:
                    raise NativeM5Refused('M5_APP_WORKFLOW_CONTROLLER_STALE_NO_PUSH_INTENT')
                _check_git_argv(['push','--porcelain','origin',
                    f"refs/heads/{bound['head']}:refs/heads/{bound['head']}"],
                    'branch_write_workflows')
            for action in ('push','pr'):
                cmd=[sys.executable,'-B','-m','devtool','--host','--no-clipboard',
                     '--repo',str(repo),'delivery','session',action,
                     '--id',sid,'--auth',auth,'--yes']
                rc,_=_run(cmd,repo=repo,timeout=480)
                if rc:
                    print('M5_NATIVE_DELIVERY_'+action.upper()+'_UNQUALIFIED rc='+str(rc)+'; read-only reconcile, NO REPOST',file=sys.stderr,flush=True)
                    return rc if rc>0 else 128-rc
            bound=_bound_status(sid,repo)
        elif op=='ci':
            auth=str(getattr(args,'auth','auto') or 'auto')
            if auth not in _AUTH:raise NativeM5Refused('M5_AUTH_MODE_UNSUPPORTED')
            checks=int(getattr(args,'checks',1) or 1)
            interval=int(getattr(args,'interval',5) or 5)
            if not 1<=checks<=60 or not 1<=interval<=120:
                raise NativeM5Refused('M5_CI_POLLING_OUT_OF_BOUNDS')
            cmd=[sys.executable,'-B','-m','devtool','--host','--no-clipboard',
                 '--repo',str(repo),'delivery','session','ci',
                 '--id',sid,'--auth',auth,
                 '--checks',str(checks),'--interval',str(interval)]
            rc,_=_run(cmd,repo=repo,timeout=checks*interval+240)
            if rc:
                print('M5_NATIVE_CI_NOT_QUALIFIED rc='+str(rc),file=sys.stderr,flush=True)
                return rc if rc>0 else 128-rc
            bound=_bound_status(sid,repo)
        elif op not in ('status','report'):
            raise NativeM5Refused('M5_NATIVE_COMMAND_UNSUPPORTED')
        if op=='report':
            from .delivery_session import report
            response=report(sid)
            if Path(str(response.get('repo_root') or '')).resolve()!=repo:
                raise NativeM5Refused('M5_REPORT_REPOSITORY_MISMATCH')
            print('M5_AUDIT_REPORT '+json.dumps({
                'session_id':sid,'recorded_ci_state':str((response.get('ci') or {}).get('state') or ''),
                'scope':'historical_read_only', 'remote_mutation':False},sort_keys=True),flush=True)
        _summary(bound,verb=op,format='json' if getattr(args,'_devtool_json',False) else 'text')
        return 0
    except (NativeM5Refused, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print('M5_NATIVE_REFUSED '+str(exc),file=sys.stderr,flush=True)
        return 2
