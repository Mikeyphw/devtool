#!/usr/bin/env python3
"""RMP-C03: source-bound native Termux formal closure of async ACK cursor underbinding.

The gate uses prior 89-test acceptance instead of replaying it. All new probes
are disposable local transport/cursor exercises; it makes no SSH peer-auth claim.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

BRANCH = 'feature/rmp01-security-remediation'
C02 = 'b448f3fca5fdf87072b3c9fb162296b65229c53e'
MESSAGE = 'audit(security): formally close RMP-C03 acknowledgement cursor binding'
CLOSE_DOC = 'docs/RMP_C03_FORMAL_CLOSURE.md'
CLOSE_MODULE = 'devtool/rmp_c03_formal_closure.py'
LEDGER = 'docs/DEVTOOL_IMPROVEMENTS.md'
TERMUX_ROOTS = ('/data/data/com.termux/files/usr', '/data/user/0/com.termux/files/usr')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inside(path: str, *, bin_only: bool = False) -> bool:
    try:
        item = Path(path).resolve(strict=False)
        return any(item.is_relative_to((Path(root) / ('bin' if bin_only else '')).resolve(strict=False)) for root in TERMUX_ROOTS)
    except (OSError, TypeError, ValueError):
        return False


def native_probe(*, platform: str | None = None, executable: str | None = None,
                 base_prefix: str | None = None, proc_executable: str | None = None) -> dict:
    platform = sys.platform if platform is None else platform
    executable = sys.executable if executable is None else executable
    base_prefix = sys.base_prefix if base_prefix is None else base_prefix
    if proc_executable is None:
        try: proc_executable = os.readlink('/proc/self/exe')
        except OSError: proc_executable = ''
    ok = platform.startswith(('android', 'linux')) and inside(base_prefix) and (inside(executable, bin_only=True) or inside(proc_executable, bin_only=True))
    return {'native_termux': ok, 'platform': platform, 'python_executable': executable,
            'python_base_prefix': base_prefix, 'proc_self_exe': proc_executable,
            'prefix_environment': os.environ.get('PREFIX', ''),
            'executable_in_termux': inside(executable, bin_only=True),
            'base_prefix_in_termux': inside(base_prefix),
            'proc_executable_in_termux': inside(proc_executable, bin_only=True)}


def git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(('git', '-C', str(repo), *arguments), capture_output=True)
    if result.returncode:
        raise RuntimeError('Git source verification refused: ' + result.stderr.decode(errors='replace')[-200:])
    return result.stdout.decode().strip()


def pin(repo: Path, *, enforce_c02: bool = True) -> tuple[str, str]:
    if git(repo, 'branch', '--show-current') != BRANCH:
        raise RuntimeError('Expected the security remediation branch')
    if git(repo, 'status', '--porcelain', '--untracked-files=no'):
        raise RuntimeError('Tracked changes or staging exist: preserve/commit before formal closure')
    if enforce_c02:
        subprocess.run(('git','-C',str(repo),'merge-base','--is-ancestor',C02,'HEAD'),check=True,stdout=subprocess.DEVNULL)
    for name in (CLOSE_DOC, CLOSE_MODULE):
        if (repo / name).exists() or (repo / name).is_symlink():
            raise RuntimeError('Existing closure file; refusal to overwrite: ' + name)
    return git(repo, 'rev-parse','HEAD'), git(repo, 'rev-parse','HEAD^{tree}')


def verify_acceptance(repo: Path, receipt: dict, head: str) -> dict:
    from devtool.rmp_c03_acceptance import TESTS, MINIMUM_CASES
    if not isinstance(receipt, dict) or receipt.get('schema_version') != 1 or receipt.get('producer') != 'devtool-rmp-c03-local-acceptance':
        raise RuntimeError('Not a C03 local acceptance receipt')
    if (receipt.get('overall') != 'PASS_C03_LOCAL_ACCEPTANCE' or type(receipt.get('pytest_passed')) is not int
        or receipt['pytest_passed'] < MINIMUM_CASES or type(receipt.get('pytest_returncode')) is not int
        or receipt['pytest_returncode'] != 0 or receipt.get('timed_out') is not False
        or receipt.get('formal_c03_closed') is not False or receipt.get('release_qualified') is not False):
        raise RuntimeError('C03 local acceptance is not fully passing and non-release')
    if receipt.get('source_commit') != head:
        raise RuntimeError('Acceptance commit differs from current HEAD: no unqualified intervening changes allowed')
    files = (*TESTS, 'devtool/distributed_transport.py', 'devtool/rmp_c03_acceptance.py')
    if len(TESTS) != 9 or len(set(TESTS)) != 9:
        raise RuntimeError('C03 lane contract changed')
    expected = {}
    for name in files:
        file = repo / name
        if file.is_symlink() or not file.is_file(): raise RuntimeError('Missing/symlinked C03 source: '+name)
        blob = file.read_bytes()
        if subprocess.run(('git','-C',str(repo),'show','HEAD:'+name),capture_output=True).stdout != blob:
            raise RuntimeError('C03 file not identical to HEAD: '+name)
        expected[name] = sha(blob)
    if receipt.get('source_sha256') != expected or receipt.get('lanes') != {name:expected[name] for name in TESTS}:
        raise RuntimeError('Acceptance receipt source/test hashes do not match current committed code')
    guard = receipt.get('guardrails') or {}
    for key, want in {'automatic_push':False,'automatic_release':False,
                      'automatic_requalification_of_historical_campaigns':False,
                      'other_rmp_findings_closed':False,'test_owned_disposable_artifacts_only':True}.items():
        if guard.get(key) is not want:raise RuntimeError('Acceptance guardrail mismatch: '+key)
    return {'pytest_passed':receipt['pytest_passed'],'lane_count':len(TESTS),
            'source_sha256':{key:expected[key] for key in files[-2:]},
            'lanes_digest':sha(json.dumps({key:expected[key] for key in TESTS},sort_keys=True).encode()),
            'finished_at_utc':receipt.get('finished_at_utc')}


def find_receipt(repo: Path, requested: Path | None, head: str) -> tuple[Path, dict, dict]:
    if requested:
        candidates=[requested.expanduser()]
    else:
        state=Path.home()/'.local/state/devtool/rmp-c03'
        candidates=sorted(state.glob('acceptance-*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:100] if state.is_dir() and not state.is_symlink() else []
    for path in candidates:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size>256000:continue
            receipt=json.loads(path.read_bytes())
            checked=verify_acceptance(repo,receipt,head)
            return path, receipt, checked
        except (OSError,ValueError,RuntimeError,TypeError,KeyError):
            if requested:raise RuntimeError('Explicit acceptance is invalid or source-mismatched: '+str(path))
    raise RuntimeError('No matching 89-test source-bound C03 receipt in ~/.local/state/devtool/rmp-c03')


def disposable_transport() -> list[dict]:
    from devtool.core.events import EventBus, RunStarted
    from devtool.core.events.listeners import event_to_dict
    from devtool.distributed_execution import DelegationLink, ExecutionRunLink, ExternalEventIngestor
    from devtool.distributed_transport import (AsyncJournalForwarder, DeliveryAck, DeliveryCursorStore,
        DelegatedEventReceiver, DistributedTransportError, LocalLoopbackTransport, OriginRunJournal)
    proof=[]
    with tempfile.TemporaryDirectory(prefix='rmp-c03-closure-') as d:
        root=Path(d)
        link=DelegationLink(run_link=ExecutionRunLink('parent','node','child'), repository='disposable',target='fixture',
                            source_lock_digest='locked', provider_identity={'provider_id':'worker-A','provider_evidence_digest':'provider-proof'},
                            attestation_digest='attestation-A')
        event=RunStarted(run_id='child',command='disposable-verify')
        event.complete_envelope(sequence=1)
        events=root/'execution-events.jsonl'
        events.write_text(json.dumps(event_to_dict(event),sort_keys=True)+'\n')
        journal=OriginRunJournal(root,child_run_id='child',link=link)
        receiver=DelegatedEventReceiver(ExternalEventIngestor(EventBus()),link=link)
        transport=LocalLoopbackTransport(receiver)
        frame=journal.frames_after(0,delivery_mode='catch-up')[0]
        valid=transport.send(frame)
        if (valid.frame_digest!=frame.frame_digest or valid.attestation_digest!=link.attestation_digest
            or not valid.session_digest):raise RuntimeError('Receiver did not produce bound ACK')
        cases={
            'legacy-unbound': DeliveryAck(link.delegation_id,link.child_run_id,1,frame.event_id),
            'frame-substitution': replace(valid,frame_digest='forged-frame'),
            'session-substitution': replace(valid,session_digest='forged-session'),
            'attestation-substitution': replace(valid,attestation_digest='forged-attestation'),
            'cross-child':replace(valid,child_run_id='other-child'),
            'bool-sequence':replace(valid,acknowledged_sequence=True),
        }
        for name,forged in cases.items():
            cursor=DeliveryCursorStore(root/(name+'.json'))
            forwarder=AsyncJournalForwarder(link=link,journal=journal,cursor_store=cursor,send_frame=lambda _frame,ack=forged:ack)
            try:forwarder.pump_once()
            except DistributedTransportError:pass
            else:raise RuntimeError('Forged ACK accepted: '+name)
            state=cursor.load(link=link)
            if forwarder.cursor.acknowledged_sequence!=0 or state.acknowledged_sequence!=0:
                raise RuntimeError('Forged ACK advanced durable cursor: '+name)
            proof.append({'case':name,'result':'PASS_DENIED_NO_CURSOR_ADVANCE',
                          'cursor_sequence':state.acknowledged_sequence})
        saved=root/'positive-cursor.json'
        store=DeliveryCursorStore(saved)
        first=AsyncJournalForwarder(link=link,journal=journal,cursor_store=store,send_frame=transport.send)
        if first.pump_once()!=1 or store.load(link=link).acknowledged_sequence!=1:
            raise RuntimeError('Valid bound ACK did not persist the durable cursor')
        snapshot=sha(saved.read_bytes())
        second=AsyncJournalForwarder(link=link,journal=journal,cursor_store=store,send_frame=transport.send)
        if second.pump_once()!=0 or second.cursor.acknowledged_sequence!=1 or sha(saved.read_bytes())!=snapshot:
            raise RuntimeError('Legitimate reconnect/replay rewrote the cursor or redelivered an event')
        proof.append({'case':'valid-bound-ack-and-reconnect','result':'PASS_DURABLE_CURSOR_AND_REPLAY',
                      'cursor_sequence':1,'cursor_sha256':snapshot})
    return proof


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() or path.is_symlink():raise RuntimeError('Evidence file already exists: '+str(path))
    with path.open('xb') as handle:
        handle.write(data);handle.flush();os.fsync(handle.fileno())


def create_overlay(repo: Path, head: str, tree: str, path: Path, additional: dict[str,bytes], modified: dict[str,bytes]) -> dict:
    inventory=[];baseline={};post={}
    for name,blob in sorted({**additional,**modified}.items()):
        row={'path':name,'action':'modify' if name in modified else 'add','sha256':sha(blob),'size':len(blob)}
        if name in modified:
            old=(repo/name).read_bytes();row['source_sha256']=sha(old);baseline[name]=sha(old)
        else:
            if (repo/name).exists() or (repo/name).is_symlink():raise RuntimeError('Closure file already present '+name)
            baseline[name]=None
        inventory.append(row);post[name]=sha(blob)
    validation=b'''#!/bin/sh\nset -eu\npython3 -c 'from pathlib import Path; p=Path("docs/RMP_C03_FORMAL_CLOSURE.md"); assert p.is_file() and "PASS_C03_FORMALLY_CLOSED" in p.read_text()'\n'''
    manifest={'schema_version':2,'type':'overlay','name':'RMP-C03 formal source-bound ACK integrity closure',
        'base_contract_mode':'touched-files','base_commit':head,'base_tree':tree,
        'touched_paths':sorted(post),'source_sha256':baseline,'expected_post_sha256':post,
        'inventory':{'format':1,'entries':inventory},
        'apply':{'atomic':True,'conflict_policy':'fail','path_policy':'repository-relative',
                 'requires_clean_worktree':False,'commit':{'enabled':True,'strategy':'single','message':MESSAGE}},
        'validation':{'required':True,'rollback_on_failure':True,'restore':False,'build':False,'test':False,
                      'script':{'path':'.devtool-artifact/validate.sh','runner':'sh','timeout_seconds':90,'sha256':sha(validation)}}}
    from devtool.artifacts import artifact_commit_policy
    if artifact_commit_policy(manifest)!=(True,MESSAGE,'single'):
        raise RuntimeError('Current Devtool did not accept auto-commit schema')
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for name,blob in sorted({**additional,**modified}.items()):z.writestr(name,blob)
        z.writestr('.devtool-artifact.json',json.dumps(manifest,sort_keys=True,indent=2)+'\n')
        z.writestr('.devtool-artifact/validate.sh',validation)
    return manifest


def main(argv: list[str]|None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--acceptance',type=Path)
    parser.add_argument('--evidence-dir',type=Path)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--allow-non-termux-test',action='store_true',help='Offline rehearsal only; never formally closes')
    args=parser.parse_args(argv)
    probe=native_probe()
    native=probe['native_termux'] and not args.allow_non_termux_test
    if not native and not args.allow_non_termux_test:
        print(json.dumps({'overall':'BLOCKED_C03_FORMAL_CLOSURE','formal_c03_closed':False,
                          'reason':'Native Termux interpreter required','environment_diagnostic':probe,'release_qualified':False},sort_keys=True))
        return 2
    repo=args.repo.expanduser().resolve(strict=True)
    try:
        head,tree=pin(repo,enforce_c02=native)
        sys.path.insert(0,str(repo))
        receipt_path,receipt,accepted=find_receipt(repo,args.acceptance,head)
        cases=disposable_transport()
        if len(cases)!=7:raise RuntimeError('Incomplete isolated transport evidence')
        outcome='PASS_C03_FORMAL_GATE' if native else 'PASS_C03_OFFLINE_REHEARSAL_ONLY'
        now=datetime.now(timezone.utc)
        stamp=now.strftime('%Y%m%dT%H%M%SZ')
        base=args.evidence_dir or Path.home()/'.local/state/devtool/rmp-c03'
        evidence_dir=base.expanduser().resolve(strict=False)
        evidence={'schema_version':1,'producer':'devtool-rmp-c03-formal-closure',
                  'overall':outcome,'formal_c03_closed':False,'release_qualified':False,
                  'qualified_source_commit':head,'qualified_source_tree':tree,
                  'native_termux':native,'environment':probe,
                  'acceptance':{'receipt_filename':receipt_path.name,'sha256':sha(receipt_path.read_bytes()),**accepted},
                  'disposable_transport':cases,
                  'original_finding':'RMP-C03: async acknowledgement cursor underbinding',
                  'residual_limits':['Only durable ACK context integrity was qualified; no hostile same-process caller security guarantee',
                                    'Not a cryptographic peer-authentication or remote trusted-execution attestation',
                                    'No real SSH worker campaign rerun or release qualification'],
                  'guardrails':{'automatic_push':False,'automatic_release':False,
                                'automatic_requalification_of_historical_campaigns':False,
                                'other_rmp_findings_closed':False,'disposable_artifacts_only':True},
                  'finished_at_utc':now.isoformat()}
        report_path=evidence_dir/('rmp-c03-formal-'+stamp+'-'+head[:8]+'.json')
        write_new(report_path,(json.dumps(evidence,sort_keys=True,indent=2)+'\n').encode())
        print('PASS_C03_EVIDENCE:',report_path,flush=True)
        if not args.apply:
            print(json.dumps({'overall':outcome,'formal_c03_closed':False,'source':head},sort_keys=True))
            return 0
        if not native:
            print('OFFLINE_REHEARSAL_ONLY: formal commit forbidden outside Termux',flush=True)
            return 0
        if (head,tree)!=pin(repo,enforce_c02=native):raise RuntimeError('Repository changed during evidence recording')
        report=f'''# RMP-C03 — formal source-bound security closure\n\n- **Decision:** `PASS_C03_FORMALLY_CLOSED`\n- **Scope:** asynchronous delivery acknowledgement context binding / durable cursor integrity\n- **Qualified source:** `{head}`; tree `{tree}`\n- **Native environment:** Termux Python runtime (platform `android` or `linux`)\n- **Reused 89-case acceptance SHA-256:** `{sha(receipt_path.read_bytes())}`\n- **Closure evidence SHA-256:** `{sha(report_path.read_bytes())}`\n- **Closure evidence file:** `{report_path}`\n- **Seven disposable E2E cases:** PASS — six acknowledgement substitution denials and one valid durable ACK/reconnect\n- **Native Git auto-commit:** requested and verified by closure installer\n- **Release qualified:** false; other RMP findings: unchanged\n\nThis is source-bound sender/receiver acknowledgement integrity closure, **not cryptographic remote peer authentication**, hostile same-UID code protection or product release qualification. C02 remains formally closed; historical G3R, CI24, SSH, Android, RS13 and remaining six audit obligations are untouched.\n'''.encode()
        old=(repo/LEDGER).read_bytes()
        addition=f'''\n## RMP-C03 formal native source-bound closure ({now.date().isoformat()})\n\n- Status: **CLOSED** within the async acknowledgement cursor-integrity scope on commit `{head}`.\n- Reused prior **89/89 PASS** C03 acceptance without replay; receipt SHA-256 `{sha(receipt_path.read_bytes())}`.\n- Native seven-case durable ACK, substitution denial and reconnection receipt SHA-256 `{sha(report_path.read_bytes())}`.\n- No claim of cryptographic peer authentication, hostile same-user process isolation, remote execution requalification or release seal.\n- Native overlay commit includes exactly the C03 closure gate, closure document and this cumulative ledger update.\n'''.encode()
        additions={CLOSE_DOC:report, CLOSE_MODULE:Path(__file__).read_bytes()}
        modified={LEDGER:old.rstrip(b'\n')+b'\n'+addition}
        with tempfile.TemporaryDirectory(prefix='rmp-c03-closure-overlay-') as td:
            overlay=Path(td)/'RMP_C03_CLOSURE.overlay.zip'
            manifest=create_overlay(repo,head,tree,overlay,additions,modified)
            env=os.environ.copy()
            for name in ('DEVTOOL_RUN_ID','DEVTOOL_TRANSACTION_REPO_ROOT','DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT',
                         'DEVTOOL_TRANSACTION_ISOLATION','DEVTOOL_VALIDATION_STATE_REPO_ROOT'):env.pop(name,None)
            env['PYTHONPATH']=str(repo)
            run=subprocess.run((sys.executable,'-m','devtool','-r',str(repo),'--yes','apply-overlay',str(overlay),'--keep-artifact'),
                               cwd=repo,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=240)
            if run.returncode:
                raise RuntimeError('Closure native Devtool commit failed: '+run.stdout.decode(errors='replace')[-900:])
            newhead=git(repo,'rev-parse','HEAD')
            changed=git(repo,'diff-tree','--no-commit-id','--name-only','-r','HEAD').splitlines()
            if newhead==head or git(repo,'log','-1','--format=%s')!=MESSAGE or sorted(changed)!=sorted(manifest['touched_paths']):
                raise RuntimeError('Auto-commit missing or changed unexpected files')
            for name,blob in {**additions,**modified}.items():
                if (repo/name).read_bytes()!=blob:raise RuntimeError('Committed file content mismatch: '+name)
            print('PASS_C03_FORMAL_COMMIT:',newhead,flush=True)
            print('PASS_C03_EXACT_COMMIT_SCOPE:',len(changed),'files',flush=True)
        final={'schema_version':1,'producer':'devtool-rmp-c03-closure-commit',
               'overall':'PASS_C03_FORMALLY_CLOSED','formal_c03_closed':True,'release_qualified':False,
               'closure_commit':newhead,'qualified_source_commit':head,
               'source_evidence_sha256':sha(report_path.read_bytes()),'evidence_path':str(report_path),
               'guardrails':evidence['guardrails']}
        final_path=evidence_dir/('rmp-c03-closed-'+stamp+'-'+newhead[:8]+'.json')
        write_new(final_path,(json.dumps(final,sort_keys=True,indent=2)+'\n').encode())
        print('PASS_C03_FINAL_RECEIPT:',final_path,flush=True)
        print(json.dumps(final,sort_keys=True))
        return 0
    except (RuntimeError,TypeError,ValueError,OSError,KeyError,subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
        print(json.dumps({'overall':'BLOCKED_C03_FORMAL_CLOSURE','formal_c03_closed':False,
                          'reason':str(exc),'release_qualified':False},sort_keys=True))
        return 2

if __name__=='__main__':raise SystemExit(main())
