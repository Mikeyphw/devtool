"""Historical qualification provenance is not live authority."""
from __future__ import annotations
import json
from pathlib import Path
from devtool.remed_r04b_fixed_point import G3R_REAL_SUBSTRATE_CARRY

ROOT=Path(__file__).resolve().parents[1]

def test_source_history_preserves_success_failures_and_scope():
    data=json.loads((ROOT/'docs/QUALIFICATION_SOURCE_HISTORY.json').read_text())
    assert data['schema_version']==1 and data['formal_release_qualified'] is False
    assert set(data['open_real_substrate_obligations'])==set(G3R_REAL_SUBSTRATE_CARRY)
    records=data['records']
    assert len({r['source_sha'] for r in records})==len(records)==3
    assert all(r['formal_release_qualified'] is False for r in records)
    assert records[0]['ci24_run_id']==38843257546
    assert records[1]['automated_milestone']=='FAILED_CI24'
    assert records[1]['ci24_shards_failed']==[12,16]
    assert records[-1]['source_sha']=='1bb1053f29ba33bf5283244b2badf3f3dccf4581'
    assert 'user-reported' in records[-1]['fs03_receipt']
    assert data['next_qualification']['state']=='PENDING_NEW_PHASE_E_COMMIT'
    assert data['next_qualification']['ci24']=='NOT_RUN'
