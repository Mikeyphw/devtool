"""Phase D provenance remains verifiable from the protected Git ancestor."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_retired_phase_d_reports_are_source_bound_and_retrievable():
    data=json.loads((ROOT/'docs/HISTORICAL_REPORT_PHASE_D_PROVENANCE.json').read_text())
    assert data['source_commit']=='f25196a297d42bc3a2ff36a03aa0411f89fe9d46'
    assert data['formal_release_qualified'] is False
    assert len(data['retired'])==4
    for item in data['retired']:
        assert not (ROOT/item['path']).exists()
        cmd=['git','show', data['source_commit']+':'+item['path']]
        proc=subprocess.run(cmd,cwd=ROOT,capture_output=True,check=False)
        assert proc.returncode==0, proc.stderr
        assert len(proc.stdout)==item['size']
        assert hashlib.sha256(proc.stdout).hexdigest()==item['sha256']

def test_phase_d_retains_compatibility_and_unresolved_release_guards():
    from devtool.compatibility import COMPATIBILITY_ENTRIES
    from devtool.remed_r04b_fixed_point import G3R_REAL_SUBSTRATE_CARRY
    data=json.loads((ROOT/'docs/HISTORICAL_REPORT_PHASE_D_PROVENANCE.json').read_text())
    assert len(COMPATIBILITY_ENTRIES)>=24
    assert set(data['unresolved_real_substrate_obligations'])==set(G3R_REAL_SUBSTRATE_CARRY)
    for path in ('DIST_G3_HISTORICAL_SUBSTRATE_CROSSWALK.json','FINAL_REMEDIATION_COVERAGE.json',
                 'PRES_X04_TEXTUAL_TUI_SUBSTRATE_REPORT.md','EXEC_X01_EXECUTION_VALIDATION_FOUNDATION_REPORT.md'):
        assert (ROOT/path).is_file()
