#!/usr/bin/env python3
"""Source-bound formal RMP-C02 closure gate. Disposable tests; no broad requalification."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_BRANCH = 'feature/rmp01-security-remediation'
EXPECTED_LANES = 11
EXPECTED_PASSED = 67
COMMIT_MESSAGE = 'audit(security): formally close RMP-C02 with native cleanup evidence'
KEEP_ENV = ('PATH', 'PREFIX', 'LD_LIBRARY_PATH', 'LANG', 'LC_ALL', 'TERM')
CLEAR_ENV = ('DEVTOOL_RUN_ID','DEVTOOL_TRANSACTION_REPO_ROOT','DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT','DEVTOOL_TRANSACTION_ISOLATION','DEVTOOL_VALIDATION_STATE_REPO_ROOT')


# Do not use PREFIX as the authority for native Termux. Customized Zsh/tmux
# sessions sometimes omit it even though Python is running from Termux's
# private Android installation. Conversely, PREFIX alone is spoofable and can
# be inherited by a non-Termux chroot interpreter.
TERMUX_USER_ROOTS = (
    Path('/data/data/com.termux/files/usr'),
    Path('/data/user/0/com.termux/files/usr'),
)


def _inside_termux_install(location: str | os.PathLike[str], *, executable: bool) -> bool:
    """Resolve Android user-0 aliases and enforce the private usr boundary."""
    try:
        path = Path(location).resolve(strict=False)
        return any(
            path.is_relative_to((root / 'bin' if executable else root).resolve(strict=False))
            for root in TERMUX_USER_ROOTS
        )
    except (ValueError, OSError, TypeError):
        return False


def detect_native_termux(*, executable: str | None = None,
                         base_prefix: str | None = None,
                         proc_executable: str | None = None,
                         platform: str | None = None,
                         env_prefix: str | None = None) -> dict:
    """Use Python's actual interpreter and installation, not only shell env.

    Keyword overrides exist for deterministic negative/positive unit probes;
    ordinary callers must not supply them. Android's /data/data and /data/user/0
    spellings are both accepted. PREFIX is diagnostic, never authoritative.
    """
    executable = sys.executable if executable is None else executable
    base_prefix = sys.base_prefix if base_prefix is None else base_prefix
    if proc_executable is None:
        try:
            proc_executable = os.readlink('/proc/self/exe')
        except OSError:
            proc_executable = ''
    platform = sys.platform if platform is None else platform
    env_prefix = os.environ.get('PREFIX', '') if env_prefix is None else env_prefix
    executable_ok = _inside_termux_install(executable, executable=True)
    proc_ok = _inside_termux_install(proc_executable, executable=True)
    base_ok = _inside_termux_install(base_prefix, executable=False)
    # CPython on Android (including recent native Termux builds) reports
    # sys.platform == 'android', not necessarily 'linux'. Runtime identity
    # is still anchored to the actual private Termux interpreter and base,
    # never to an exported shell PREFIX alone.
    native = platform.startswith(('linux', 'android')) and base_ok and (executable_ok or proc_ok)
    return {
        'native_termux': native,
        'platform': platform,
        'python_executable': executable,
        'python_base_prefix': base_prefix,
        'proc_self_exe': proc_executable,
        'prefix_environment': env_prefix,
        'python_executable_in_termux': executable_ok,
        'proc_executable_in_termux': proc_ok,
        'python_base_prefix_in_termux': base_ok,
    }


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(p: Path) -> str:
    return sha(p.read_bytes())


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, check=False)
    if p.returncode:
        raise RuntimeError('Git check refused ('+' '.join(args)+'): '+p.stderr.decode(errors='replace')[:220])
    return p.stdout.decode().strip()


def repo_check(repo: Path, *, closure_file: str) -> tuple[str,str]:
    if git(repo,'branch','--show-current') != REQUIRED_BRANCH:
        raise RuntimeError('Wrong branch: expected '+REQUIRED_BRANCH)
    if git(repo,'status','--porcelain','--untracked-files=no'):
        raise RuntimeError('Tracked source or index has uncommitted changes; preserve and commit them before closure')
    if (repo/closure_file).exists() or (repo/closure_file).is_symlink():
        raise RuntimeError('Closure record exists; refusing to overwrite or re-close')
    required=['devtool/artifact_cleanup_worker.py','devtool/cli.py','devtool/rmp_c02_acceptance.py',
              'docs/DEVTOOL_IMPROVEMENTS.md','tests/test_rmp_c02_acceptance.py']
    if any(not (repo/p).is_file() or (repo/p).is_symlink() for p in required):
        raise RuntimeError('Missing expected committed C02 source')
    return git(repo,'rev-parse','HEAD'), git(repo,'rev-parse','HEAD^{tree}')


def verify_acceptance(repo: Path, receipt: dict) -> dict:
    from devtool.rmp_c02_acceptance import LANES
    if not isinstance(receipt, dict) or receipt.get('schema_version') != 1 or receipt.get('producer') != 'devtool-rmp-c02-local-acceptance':
        raise RuntimeError('Unrecognized C02 acceptance receipt')
    if (receipt.get('overall') != 'PASS_C02_LOCAL_ACCEPTANCE' or receipt.get('pytest_passed') != EXPECTED_PASSED
        or type(receipt.get('pytest_returncode')) is not int or receipt['pytest_returncode'] != 0
        or receipt.get('timed_out') is not False or receipt.get('formal_c02_closed') is not False
        or receipt.get('release_qualified') is not False):
        raise RuntimeError('Local acceptance receipt is not a passing 67-test nonrelease C02 receipt')
    if len(LANES) != EXPECTED_LANES or len(set(LANES)) != EXPECTED_LANES or set(receipt.get('lanes',{})) != set(LANES):
        raise RuntimeError('Local acceptance lane inventory changed or incomplete')
    expected = {p:digest(repo/p) for p in LANES}
    if expected != receipt['lanes']:
        raise RuntimeError('Local acceptance source/test digest mismatch: do not reuse evidence for different code')
    src={'artifact_cleanup_worker_sha256':digest(repo/'devtool/artifact_cleanup_worker.py'),
         'cli_sha256':digest(repo/'devtool/cli.py')}
    if receipt.get('source') != src:
        raise RuntimeError('Receipt was produced for a different cleanup worker or CLI')
    guard=receipt.get('guardrails') or {}
    if (guard.get('test_owned_temporary_artifacts_only') is not True
        or guard.get('automatic_requalification_of_historical_campaigns') is not False
        or guard.get('automatic_push') is not False or guard.get('automatic_release') is not False):
        raise RuntimeError('Acceptance guardrails absent or incompatible')
    return {'lane_count':len(LANES),'passed':EXPECTED_PASSED,'source':src,
            'lanes_sha256':sha(json.dumps(expected,sort_keys=True).encode()),
            'finished_at_utc':receipt.get('finished_at_utc')}


def resolve_acceptance(repo: Path, requested: str | None) -> tuple[dict,Path]:
    candidates=[]
    if requested:
        candidates=[Path(requested).expanduser()]
    else:
        directory=Path.home()/'.local/state/devtool/rmp-c02'
        if directory.is_dir() and not directory.is_symlink():
            candidates=sorted(directory.glob('*.json'),key=lambda x:x.stat().st_mtime,reverse=True)[:100]
    for p in candidates:
        try:
            if p.is_symlink() or not p.is_file() or p.stat().st_size > 256000:continue
            obj=json.loads(p.read_bytes())
            verify_acceptance(repo,obj)
            return obj,p
        except (RuntimeError,ValueError,TypeError,OSError,KeyError,json.JSONDecodeError):
            if requested:raise RuntimeError('Explicit acceptance receipt is absent, invalid, or source-mismatched: '+str(p))
    raise RuntimeError('No matching local 67/67 receipt under ~/.local/state/devtool/rmp-c02; specify --acceptance PATH')


def run(command: list[str], *, cwd: Path, env: dict, timeout: int) -> tuple[int,str,float]:
    import time
    t=time.monotonic()
    try:
        p=subprocess.run(command,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                         text=True,timeout=timeout,check=False)
        return p.returncode,p.stdout,time.monotonic()-t
    except subprocess.TimeoutExpired as exc:
        return 124, 'TIMEOUT: '+' '.join(command[:4]),time.monotonic()-t


def fixture_overlay(path: Path, repo: Path, *, new: bytes) -> None:
    name='fixture.txt';old=(repo/name).read_bytes()
    manifest={'schema_version':2,'type':'overlay','name':'RMP-C02 disposable closure proof',
              'base_contract_mode':'touched-files','base_commit':git(repo,'rev-parse','HEAD'),
              'base_tree':git(repo,'rev-parse','HEAD^{tree}'), 'touched_paths':[name],
              'source_sha256':{name:sha(old)},'expected_post_sha256':{name:sha(new)},
              'inventory':{'format':1,'entries':[{'path':name,'action':'modify','sha256':sha(new),
                          'size':len(new),'source_sha256':sha(old)}]},
              'apply':{'atomic':True,'conflict_policy':'fail','path_policy':'repository-relative',
                       'commit':{'enabled':False}},'validation':{'required':False}}
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr(name,new);z.writestr('.devtool-artifact.json',json.dumps(manifest,sort_keys=True))


def fixture_bundle(bundle_path: Path, nested: Path) -> None:
    data=nested.read_bytes();member='overlays/001_disposable.zip'
    rows=[{'id':'fixture','path':member,'required':True,'sha256':sha(data),'size':len(data)}]
    manifest={'schema_version':2,'type':'overlay_bundle','kind':'devtool.overlay_bundle',
              'name':'RMP-C02 disposable explicit bundle','target_mode':'per_overlay',
              'overlays':rows,'apply_order':rows,
              'apply':{'atomic':True,'conflict_policy':'fail','path_policy':'repository-relative',
                       'commit':{'enabled':False,'strategy':'single','message':''}},
              'validation':{'required':False,'strategy':'per-overlay','rollback_on_failure':True,'failure_action':'pause'}}
    with zipfile.ZipFile(bundle_path,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('devtool-bundle.json',json.dumps(manifest,sort_keys=True));z.writestr(member,data)


def receipt_proof(repo: Path, artifact: Path) -> dict:
    directory=repo/'.devtool/private/cleanup/receipts'
    if not directory.is_dir() or directory.is_symlink():raise RuntimeError('Missing LIFE receipt directory')
    matches=[]
    for p in directory.glob('cleanup-receipt-*.json'):
        if p.is_symlink() or p.stat().st_size>65536:continue
        try:row=json.loads(p.read_bytes())
        except (OSError,ValueError):continue
        results=row.get('results')
        if (row.get('scope')=='project' and row.get('destination')=='local' and row.get('status')=='applied'
            and isinstance(results,list) and len(results)==1
            and results[0].get('path')==str(artifact)
            and results[0].get('status')=='applied' and results[0].get('postcondition')=='absent'
            and p.name==str(row.get('receipt_id','')).replace(':','-')+'.json'):
            matches.append({'receipt_sha256':digest(p),'receipt_id':row['receipt_id']})
    if len(matches)!=1:raise RuntimeError('Expected exactly one matching successful LIFE receipt, found '+str(len(matches)))
    return matches[0]


def source_env(repo: Path, scratch: Path) -> dict:
    env=os.environ.copy()
    for item in CLEAR_ENV:env.pop(item,None)
    env['PYTHONPATH']=str(repo)
    for name,folder in {'HOME':'home','XDG_STATE_HOME':'state','XDG_CACHE_HOME':'cache','XDG_DATA_HOME':'data'}.items():
        target=scratch/folder;target.mkdir(exist_ok=True)
        env[name]=str(target)
    env['DEVTOOL_COLOR']='0'
    return env


def substrate(repo: Path, *, timeout: int) -> list[dict]:
    cases=[]
    with tempfile.TemporaryDirectory(prefix='rmp-c02-formal-',dir=None) as temp:
        base=Path(temp).resolve()
        for kind in ('overlay','bundle'):
            work=base/kind;work.mkdir()
            target=work/'repo';target.mkdir()
            (target/'fixture.txt').write_bytes(b'original\n')
            env=source_env(repo,work)
            for command in ([ 'git','init','-q'], ['git','config','user.name','Devtool C02 Fixture'],
                            ['git','config','user.email','c02-fixture@example.invalid'],
                            ['git','add','fixture.txt'],['git','commit','-qm','disposable base']):
                code,output,_=run(command,cwd=target,env=env,timeout=20)
                if code:raise RuntimeError('Unable to construct disposable Git fixture ('+kind+'): '+output[-250:])
            nested=work/'nested_source.zip'; fixture_overlay(nested,target,new=(kind+'-applied\n').encode())
            if kind=='overlay':
                consumed=work/'selected-overlay-4.zip'
                consumed.write_bytes(nested.read_bytes())
            else:
                consumed=work/'selected-bundle-4.zip'
                fixture_bundle(consumed,nested)
            sibling=work/'unrelated-sibling.zip'; sibling.write_bytes(b'unrelated sibling private file')
            sidecar=work/'unrelated-sibling.sha256';sidecar.write_bytes(b'sidecar unaffected')
            unrelated=work/'outside-victim.zip';unrelated.write_bytes(b'not-authorized')
            assert zipfile.is_zipfile(consumed), ('fixture built invalid zip',consumed,consumed.stat().st_size,consumed.read_bytes()[:40])
            snapshots={str(x):sha(x.read_bytes()) for x in (sibling,sidecar,unrelated,nested)}
            code,output,duration=run([sys.executable,'-m','devtool','-r',str(target),'--yes','apply-overlay',str(consumed)],
                                      cwd=target,env=env,timeout=timeout)
            if code or consumed.exists() or consumed.is_symlink():
                raise RuntimeError(kind+' actual apply/cleanup failed, returned '+str(code)+'; '+output[-600:])
            if (target/'fixture.txt').read_bytes()!=(kind+'-applied\n').encode():
                raise RuntimeError(kind+' applied wrong repository content')
            if any(not x.is_file() or sha(x.read_bytes())!=expected for x,expected in [(Path(k),v) for k,v in snapshots.items()]):
                raise RuntimeError(kind+' cleanup deleted/changed an unrelated fixture archive or sidecar')
            proof=receipt_proof(target,consumed)
            cases.append({'kind':kind,'exit_code':0,'elapsed_seconds':round(duration,3),
                          'consumed_source_deleted':True,'unrelated_artifacts_preserved':True,
                          'repository_payload_verified':True,'life_receipt':proof,
                          'cli_output_sha256':sha(output.encode()),'fixture_source_sha256':sha((kind+'-applied\n').encode())})
    return cases


def negative(repo: Path,timeout: int) -> dict:
    with tempfile.TemporaryDirectory(prefix='rmp-c02-negative-') as tmp:
        root=Path(tmp);own=root/'repo';own.mkdir();untouched=root/'victim.zip';untouched.write_bytes(b'outside, unconsumed')
        env=source_env(repo,root)
        command=[sys.executable,'-m','devtool.artifact_cleanup_worker','--repo-root',str(own),'--reason',
                 'after successful apply','--',str(untouched)]
        code,output,_=run(command,cwd=own,env=env,timeout=min(timeout,40))
        if code==0 or not untouched.is_file() or untouched.read_bytes()!=b'outside, unconsumed':
            raise RuntimeError('Direct external cleanup without inherited authorization did not fail closed')
        try:payload=json.loads(output.strip().splitlines()[-1])
        except (ValueError,IndexError):raise RuntimeError('Direct worker denial produced no structured evidence')
        if type(payload.get('returncode')) is not int or payload['returncode']==0:
            raise RuntimeError('Direct worker denial is not a structured refusal')
        # Reject leaf symlink at original selection boundary.
        actual=root/'real.zip';actual.write_bytes(b'private file')
        alias=root/'linked.zip';alias.symlink_to(actual)
        from devtool.artifacts import resolve_artifact_path
        try:resolve_artifact_path(alias)
        except ValueError:pass
        else:raise RuntimeError('Symlinked artifact selection was allowed')
        if not actual.is_file() or not alias.is_symlink():raise RuntimeError('Symlink test altered a fixture')
        return {'direct_worker_denied':True,'victim_unchanged':True,'symlink_input_denied':True,
                'denial_output_sha256':sha(output.encode())}


def write_new(path: Path,content: str|bytes) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink() or path.exists():raise RuntimeError('Refusing to overwrite evidence: '+str(path))
    with path.open('xb') as handle:handle.write(content.encode() if isinstance(content,str) else content)


def make_overlay(repo: Path, head: str, tree: str, output: Path, additions: dict[str,bytes], modified:dict[str,bytes]) -> dict:
    inventory=[];base={};post={}
    for path,data in sorted({**additions,**modified}.items()):
        row={'path':path,'action':'modify' if path in modified else 'add','sha256':sha(data),'size':len(data)}
        if path in modified:
            old=(repo/path).read_bytes()
            row['source_sha256']=sha(old);base[path]=sha(old)
        else:
            if (repo/path).exists():raise RuntimeError('Unexpected preexisting closure artifact '+path)
            base[path]=None
        inventory.append(row);post[path]=sha(data)
    # Use a Python validator instead of shell quoting complexity.
    validation=b'''#!/bin/sh\nset -eu\npython3 -c 'from pathlib import Path; p=Path("docs/RMP_C02_FORMAL_CLOSURE.md"); assert p.is_file() and "PASS_C02_FORMALLY_CLOSED" in p.read_text()'\n'''
    manifest={'schema_version':2,'type':'overlay','name':'RMP-C02 formal source-bound closure with native evidence',
              'base_contract_mode':'touched-files','base_commit':head,'base_tree':tree,
              'touched_paths':sorted(post),'source_sha256':base,'expected_post_sha256':post,
              'inventory':{'format':1,'entries':inventory},
              'apply':{'atomic':True,'conflict_policy':'fail','path_policy':'repository-relative',
                       'requires_clean_worktree':False,
                       'commit':{'enabled':True,'strategy':'single','message':COMMIT_MESSAGE}},
              'validation':{'required':True,'rollback_on_failure':True,'restore':False,'build':False,'test':False,
                            'script':{'path':'.devtool-artifact/validate.sh','runner':'sh','timeout_seconds':90,
                                      'sha256':sha(validation)}}}
    from devtool.artifacts import artifact_commit_policy
    if artifact_commit_policy(manifest)!=(True,COMMIT_MESSAGE,'single'):
        raise RuntimeError('Active Devtool does not accept native auto-commit schema')
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for path,data in sorted({**additions,**modified}.items()):z.writestr(path,data)
        z.writestr('.devtool-artifact.json',json.dumps(manifest,sort_keys=True,indent=2)+'\n')
        z.writestr('.devtool-artifact/validate.sh',validation)
    return manifest


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--acceptance',help='Existing on-device 67/67 JSON receipt; auto-discover otherwise')
    parser.add_argument('--evidence-dir',type=Path,help='Exclusive-write evidence directory (default ~/.local/state/devtool/rmp-c02)')
    parser.add_argument('--timeout',type=int,default=100,help='Per disposable apply deadline in seconds')
    parser.add_argument('--apply',action='store_true',help='After PASS, commit closure document through Devtool')
    parser.add_argument('--allow-non-termux-test',action='store_true',help='Offline simulation only; never marks formal closure')
    args=parser.parse_args()
    if not 30<=args.timeout<=300:parser.error('timeout must be 30–300')
    repo=args.repo.expanduser().resolve(strict=True)
    native_probe = detect_native_termux()
    termux = native_probe['native_termux']
    native = termux and not args.allow_non_termux_test
    if not native and not args.allow_non_termux_test:
        print(json.dumps({'overall':'BLOCKED_C02_FORMAL_CLOSURE',
                          'formal_c02_closed':False,'release_qualified':False,
                          'reason':'Native Termux Python installation required; shell PREFIX alone is not proof',
                          'environment_diagnostic':native_probe},sort_keys=True))
        return 2
    try:
        # Pin the packaged gate to actual local source and require an unmodified tracked tree.
        closure_doc='docs/RMP_C02_FORMAL_CLOSURE.md'
        head,tree=repo_check(repo,closure_file=closure_doc)
        if str(repo) not in sys.path: sys.path.insert(0,str(repo))
        import devtool
        if not Path(devtool.__file__).resolve().is_relative_to(repo):
            raise RuntimeError('Imported Devtool is not from the qualified repository')
        receipt,path=resolve_acceptance(repo,args.acceptance)
        accepted=verify_acceptance(repo,receipt)
        evidence_dir=(args.evidence_dir or Path.home()/'.local/state/devtool/rmp-c02').expanduser().absolute()
        if evidence_dir.is_symlink():raise RuntimeError('Evidence directory is a symlink')
        evidence_dir.mkdir(parents=True,exist_ok=True)
        # Reuse evidence in place. Never rerun the 67 tests or modify their receipt.
        caller=negative(repo,args.timeout)
        cases=substrate(repo,timeout=args.timeout)
        status='PASS_C02_NATIVE_PROOFS_PENDING_COMMIT' if native else 'PASS_C02_OFFLINE_SIMULATION_NOT_CLOSED'
        finish=datetime.now(timezone.utc).isoformat()
        verdict={'schema_version':1,'producer':'devtool-rmp-c02-formal-closure',
                 'overall':status,'formal_c02_closed':False,'release_qualified':False,
                 'branch':REQUIRED_BRANCH,'qualified_source_commit':head,'qualified_source_tree':tree,
                 'environment':{'native_termux':native,'platform':sys.platform,
                                'termux_python_installation_detected':termux,
                                'termux_runtime_probe':native_probe},
                 'acceptance':{'receipt_sha256':digest(path),'receipt_filename':path.name,**accepted},
                 'e2e':cases,'negative':caller,'finished_at_utc':finish,
                 'original_finding':'RMP-C02: canonical external consumed-source deletion authority',
                 'residual_limits':['No claim against arbitrary code with the same OS user permissions',
                                    'Local source-bound cleanup qualification only; no release or remote CI seal'],
                 'guardrails':{'automatic_push':False,'automatic_release':False,
                               'automatic_requalification_of_historical_campaigns':False,
                               'disposable_artifacts_only':True,
                               'other_rmp_findings_closed':False}}
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        recorded=evidence_dir/('rmp-c02-formal-'+stamp+'-'+head[:8]+'.json')
        write_new(recorded,json.dumps(verdict,indent=2,sort_keys=True)+'\n')
        print('PASS_C02_EVIDENCE:',recorded,flush=True)
        if not args.apply:
            print(json.dumps(verdict,sort_keys=True));return 0
        if not native:
            print('OFFLINE_SIMULATION_ONLY: closure commit prohibited outside native Termux',file=sys.stderr)
            return 0
        # Recheck source and index before mutation, and commit only the 3 audit files.
        newhead,newtree=repo_check(repo,closure_file=closure_doc)
        if (newhead,newtree)!=(head,tree):raise RuntimeError('Source changed during closure gate')
        report='''# RMP-C02 — formal source-bound closure\n\n'''+f'''- **Decision:** `PASS_C02_FORMALLY_CLOSED`\n- **Qualified source commit:** `{head}`\n- **Qualified source tree:** `{tree}`\n- **Native environment:** Termux (confirmed by executable gate)\n- **Existing 67/67 acceptance evidence SHA-256:** `{digest(path)}`\n- **Formal gate receipt SHA-256:** `{digest(recorded)}`\n- **Formal gate receipt location at qualification:** `{str(recorded)}`\n- **Disposable real apply:** PASS, external consumed source only\n- **Disposable explicit bundle:** PASS, bundle source only\n- **Negative direct caller and symlink-input:** PASS (refused without collateral mutation)\n- **LIFE cleanup receipts:** recorded for both positive cases (digests in external JSON receipt)\n- **Security boundary:** process-local source/receipt authorization; not hostile arbitrary same-UID code\n- **Release qualification:** false; **other RMP finding closure:** unchanged\n\nThe original historical finding, 308-row audit, six independent audit obligations, G3R, CI24, Android, SSH and RS13 records remain preserved. This is RMP-C02 **source-bound security qualification closure only**, not a product release seal.\n'''
        existing=(repo/'docs/DEVTOOL_IMPROVEMENTS.md').read_bytes()
        improvement=f'''\n## RMP-C02 formal source-bound security closure ({finish[:10]})\n\n- Status: **CLOSED at native Termux source-bound cleanup scope**, conditional on the linked local evidence receipt and exact qualified source commit `{head}`.\n- Evidence digest: `{digest(recorded)}`; both real disposable caller paths passed, plus 67 historical source-pinned local acceptance tests reused without rerun.\n- Original external cleanup vulnerability, hostile same-UID limitations and unrelated RMP/audit/release obligations remain explicitly separated.
- Closure gate compatibility issue: native Android CPython reports `sys.platform=android`; corrected overly strict Linux-only detection while retaining Termux binary/base-prefix path verification.\n'''.encode()
        addition={'docs/RMP_C02_FORMAL_CLOSURE.md':report.encode(),
                  'devtool/rmp_c02_formal_closure.py':Path(__file__).read_bytes()}
        modified={'docs/DEVTOOL_IMPROVEMENTS.md':existing+improvement}
        with tempfile.TemporaryDirectory(prefix='rmp-c02-closure-overlay-') as tmp:
            archive=Path(tmp)/'RMP_C02_FORMAL_CLOSURE.overlay.zip'
            manifest=make_overlay(repo,head,tree,archive,addition,modified)
            env=os.environ.copy()
            for key in CLEAR_ENV:env.pop(key,None)
            env['PYTHONPATH']=str(repo)
            code,output,_=run([sys.executable,'-m','devtool','-r',str(repo),'--yes','apply-overlay',str(archive),'--keep-artifact'],
                               cwd=repo,env=env,timeout=240)
            if code:
                raise RuntimeError('Native commit overlay failed: '+output[-700:])
            final=git(repo,'rev-parse','HEAD')
            paths=git(repo,'diff-tree','--no-commit-id','--name-only','-r','HEAD').splitlines()
            expected=sorted(list(addition)+list(modified))
            if final==head or git(repo,'log','-1','--format=%s')!=COMMIT_MESSAGE or sorted(paths)!=expected:
                raise RuntimeError('Overlay did not create exactly the required native Git commit')
            for name,blob in {**addition,**modified}.items():
                if (repo/name).read_bytes()!=blob:raise RuntimeError('Committed closure file content differs: '+name)
            print('PASS_C02_FORMAL_COMMIT:',final,flush=True)
            print('PASS_C02_EXACT_COMMIT_SCOPE:',len(paths),'files',flush=True)
        closure={'schema_version':1,'producer':'devtool-rmp-c02-closure-commit',
                 'overall':'PASS_C02_FORMALLY_CLOSED','formal_c02_closed':True,
                 'release_qualified':False,'closure_commit':final,
                 'qualified_source_commit':head,'source_evidence_sha256':digest(recorded),
                 'evidence_path':str(recorded),'guardrails':verdict['guardrails']}
        final_path=evidence_dir/('rmp-c02-closed-'+stamp+'-'+final[:8]+'.json')
        write_new(final_path,json.dumps(closure,sort_keys=True,indent=2)+'\n')
        print('PASS_C02_FINAL_RECEIPT:',final_path,flush=True)
        print(json.dumps(closure,sort_keys=True))
        return 0
    except (OSError,RuntimeError,ValueError,KeyError,TypeError,zipfile.BadZipFile) as exc:
        print(json.dumps({'overall':'BLOCKED_C02_FORMAL_CLOSURE','formal_c02_closed':False,
                          'release_qualified':False,'reason':str(exc)},sort_keys=True))
        return 2

if __name__=='__main__':
    sys.exit(main())