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

def run_gate() -> dict[str, Any]:
    """Retired historical attestation gate; never infer release status."""
    raise RuntimeError("ANDROID-G3 historical seal retired; use G3R v3 current-source evidence and native receipts")


def main() -> int:
    try:
        print(json.dumps(run_gate(),sort_keys=True)); return 0
    except Exception as exc:
        print(f'ANDROID-G3 failed: {exc}',file=sys.stderr); return 1

if __name__=='__main__': raise SystemExit(main())
