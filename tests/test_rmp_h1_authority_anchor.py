from __future__ import annotations
import csv
import json
from pathlib import Path
import pytest
from devtool.rmp_h1_acceptance import authority_check, ANCHOR

def test_reconciled_authority_matches_historical_anchor():
    root=Path(__file__).resolve().parents[1]
    result=authority_check(root)
    assert result['rows']==308
    assert result['formal_n01_closed'] is False
    assert result['authoritative_signature_verified'] is False

def test_roadmap_row_substitution_rejected(tmp_path):
    root=Path(__file__).resolve().parents[1]
    (tmp_path/'docs').mkdir()
    (tmp_path/ANCHOR).write_bytes((root/ANCHOR).read_bytes())
    original=json.loads((root/'FINAL_REMEDIATION_COVERAGE.json').read_text())
    original['finding_assignments'][0]['overlay']=14
    (tmp_path/'FINAL_REMEDIATION_COVERAGE.json').write_text(json.dumps(original))
    with pytest.raises(ValueError,match='no longer matches'):
        authority_check(tmp_path)

def test_anchor_replacement_rejected(tmp_path):
    root=Path(__file__).resolve().parents[1]
    (tmp_path/'docs').mkdir()
    (tmp_path/ANCHOR).write_bytes((root/ANCHOR).read_bytes()+b'FORGED\n')
    (tmp_path/'FINAL_REMEDIATION_COVERAGE.json').write_bytes((root/'FINAL_REMEDIATION_COVERAGE.json').read_bytes())
    with pytest.raises(ValueError,match='modified'):
        authority_check(tmp_path)
