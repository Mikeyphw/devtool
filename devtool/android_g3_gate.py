from __future__ import annotations

"""ANDROID-G3 final fixed-point proof.

This is gate tooling, not a second Android execution engine. It composes the
qualified ANDROID-G1/G2 process proofs with X04 advanced-plan contracts and
verifies final source-disposition/consumer evidence without mutating the repo.
"""

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterator

from .android_g1_gate import run_gate as run_g1
from .android_g2_gate import run_gate as run_g2
from .android.advanced_execution import (
    AndroidTestOrchestratorMode, DeviceCapacity, ExoRetryPolicy, QuarantinePolicy,
    build_advanced_instrumentation_plan, execution_profile, matrix_evidence,
)
from .android.execution_snapshot import DeviceLease
from .android.instrumentation import InstrumentationRequest, instrumentation_intent

ROOT=Path(__file__).resolve().parents[1]

class AndroidG3Error(RuntimeError): pass

def _require(v: bool, message: str) -> None:
    if not v: raise AndroidG3Error(message)

def _is_termux() -> bool:
    prefix=str(os.environ.get('PREFIX') or '')
    return prefix.startswith('/data/data/com.termux/') or '/com.termux/' in sys.executable

def _write_exe(path: Path, body: str) -> Path:
    path.write_text('#!/bin/sh\nset -eu\n'+body,encoding='utf-8'); path.chmod(0o755); return path

@contextmanager
def _construction_host_tools() -> Iterator[None]:
    if _is_termux():
        yield; return
    _require(os.environ.get('DEVTOOL_ANDROID_G3_ALLOW_TEST_HOST')=='1','ANDROID-G3 authoritative proof requires native Termux')
    old=dict(os.environ)
    with tempfile.TemporaryDirectory(prefix='devtool-android-g3-tools-') as td:
        root=Path(td)
        gradle=_write_exe(root/'gradle', "mkdir -p build/outputs/apk/release\nprintf 'android-g1-app' > build/outputs/apk/release/app-release.apk\nexit 0\n")
        java=_write_exe(root/'java', "printf 'openjdk version \\\"17\\\"\\n'\nexit 0\n")
        adb=_write_exe(root/'adb', "if [ \"${1:-}\" = version ]; then printf 'Android Debug Bridge version 1.0.41\\nVersion 36.0.0\\n'; exit 0; fi\nif [ \"${1:-}\" = devices ]; then printf 'List of devices attached\\n'; exit 0; fi\nexit 0\n")
        os.environ['DEVTOOL_ANDROID_G1_ALLOW_TEST_HOST']='1'
        os.environ['DEVTOOL_ANDROID_G1_GRADLE']=str(gradle)
        os.environ['DEVTOOL_ANDROID_G1_JAVA']=str(java)
        os.environ['DEVTOOL_ANDROID_G1_ADB']=str(adb)
        os.environ['DEVTOOL_ANDROID_G2_ALLOW_TEST_HOST']='1'
        try: yield
        finally:
            os.environ.clear(); os.environ.update(old)

def _git_status() -> str:
    cp=subprocess.run(['git','status','--porcelain=v1'],cwd=ROOT,text=True,capture_output=True,check=False)
    return cp.stdout if cp.returncode==0 else ''

def _hash(path: Path) -> str: return sha256(path.read_bytes()).hexdigest()

def run_gate() -> dict[str,Any]:
    from .g3_evidence_preflight import assess_evidence
    preflight = assess_evidence(ROOT, {'android-source': 'ANDROID_G3_SOURCE_DISPOSITIONS.json'},
                                {'android': 'ANDROID_G3_FINAL_ATTESTATION.json'})
    _require(preflight['status'] == 'consistent',
             f"ANDROID-G3 historical evidence preflight blocked ({preflight['issue_count']} issues)")
    before=_git_status()
    disp_path=ROOT/'ANDROID_G3_SOURCE_DISPOSITIONS.json'
    att_path=ROOT/'ANDROID_G3_FINAL_ATTESTATION.json'
    disp=json.loads(disp_path.read_text())
    _require(disp.get('source_obligations')==168 and len(disp.get('rows') or [])==168,'ANDROID-G3 source dispositions incomplete')
    evidence_items=[]
    for row in disp['rows']:
        refs=row.get('evidence') or []
        hashes=row.get('evidence_sha256') or {}
        _require(set(refs)==set(hashes),f"ANDROID-G3 evidence binding incomplete for {row.get('source_id')}")
        for rel in refs:
            path=ROOT/rel
            _require(path.is_file(),f"ANDROID-G3 evidence missing: {rel}")
            digest=_hash(path)
            _require(digest==hashes[rel],f"ANDROID-G3 evidence drift: {rel}")
            evidence_items.append(f"{row['source_id']}\0{rel}\0{digest}")
    att=json.loads(att_path.read_text())
    _require(att.get('source_dispositions_sha256')==_hash(disp_path),'ANDROID-G3 attestation/disposition digest mismatch')
    evidence_root=sha256(('\n'.join(sorted(evidence_items))+'\n').encode()).hexdigest()
    _require(att.get('evidence_root_sha256')==evidence_root,'ANDROID-G3 attestation/evidence-root mismatch')
    _require(att.get('evidence_bindings')==len(evidence_items),'ANDROID-G3 attestation/evidence-count mismatch')
    if _is_termux():
        _require(os.environ.get('DEVTOOL_ANDROID_G3_ALLOW_TEST_HOST')!='1','ANDROID-G3 construction-host bypass must not be enabled on native Termux')
    with _construction_host_tools():
        g1=run_g1(); g2=run_g2()
    lease=DeviceLease(
        lease_id=str(g1['device_lease_id']),attempt_id='android-g3',requirements_id='android-g3-req',
        transport_id=str(g1['transport_id']),serial=str(g1['device_serial']),transport_serial='g3',
        api_level='35',abi='arm64-v8a',connection_type='tcp',model='ANDROID-G3',boot_completed='1',
        freshness_id='android-g3-fresh',device={'serial':str(g1['device_serial']),'emulator':False,'physical':True},
    )
    intent=instrumentation_intent(InstrumentationRequest(ROOT,policy='required',classes=['devtool.G3Test'],methods=['devtool.G3Test#method']))
    plan=build_advanced_instrumentation_plan(
        intent,device_capacities=(DeviceCapacity(lease,max_concurrency=1),),shard_count=2,workers=2,
        orchestrator=AndroidTestOrchestratorMode(enabled=True,qualification_id='android-g3-orchestrator',target_instrumentation='devtool.test/Runner'),
        retry=ExoRetryPolicy(mode='failed-only',max_attempts=2),
        quarantine=QuarantinePolicy(('devtool.G3Flaky',),reason='android-g3-evidence-only'),
        profile=execution_profile(ui_automator=True,cross_app=True),
    )
    evidence=matrix_evidence(plan,tuple({'child_id':c.child_id,'status':'passed'} for c in plan.children))
    _require(evidence['complete'] is True,'ANDROID-G3 advanced matrix evidence incomplete')
    _require(plan.retry.owner=='EXO' and plan.profile.family=='ui-automator','ANDROID-G3 external authority/profile drift')
    child=subprocess.run([
        sys.executable,'-c',
        "from pathlib import Path; from devtool.android.instrumentation import InstrumentationRequest,instrumentation_intent; i=instrumentation_intent(InstrumentationRequest(Path('.'),policy='required',classes=['devtool.G3Test'])); print(i.intent_id,len(i.classes))"
    ],cwd=ROOT,text=True,capture_output=True,check=False,timeout=30)
    _require(child.returncode==0 and child.stdout.strip().endswith(' 1'),'ANDROID-G3 fresh-process intent normalization failed')
    after=_git_status(); _require(after==before,'ANDROID-G3 final proof mutated repository state')
    return {
        'schema_version':1,'gate':'ANDROID-G3','status':'passed','native_termux':_is_termux(),
        'g1_environment_id':g1['environment_id'],'g1_snapshot_id':g1['snapshot_id'],'g1_device_lease_id':g1['device_lease_id'],
        'g2_intent_id':g2['intent_id'],'g2_plan_id':g2['plan_id'],'advanced_plan_id':plan.plan_id,
        'advanced_children':len(plan.children),'advanced_waves':len(plan.execution_waves),'orchestrator':plan.orchestrator.enabled,
        'ui_automator':plan.profile.ui_automator,'retry_owner':plan.retry.owner,'matrix_complete':evidence['complete'],
        'source_dispositions_sha256':_hash(disp_path),'fresh_process':child.stdout.strip(),
    }

def main() -> int:
    try:
        print(json.dumps(run_gate(),sort_keys=True)); return 0
    except Exception as exc:
        print(f'ANDROID-G3 failed: {exc}',file=sys.stderr); return 1

if __name__=='__main__': raise SystemExit(main())
