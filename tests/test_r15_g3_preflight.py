"""R15: fail-closed G3 evidence, without forging or rewriting seals."""
import hashlib
import json
from pathlib import Path
from devtool.g3_evidence_preflight import assess_evidence

def sample(tmp_path):
    p=tmp_path/'evidence.txt'; p.write_text('proof')
    digest=hashlib.sha256(p.read_bytes()).hexdigest()
    d={'rows':[{'source_id':'ART-P001','evidence':['evidence.txt'],
                'evidence_sha256':{'evidence.txt':digest}}]}
    (tmp_path/'dispositions.json').write_text(json.dumps(d))
    (tmp_path/'attestation.json').write_text('{"status":"candidate"}')
    return d

def assess(tmp_path):
    return assess_evidence(tmp_path, {'sample':'dispositions.json'}, {'sample':'attestation.json'})

def test_matching_pins_report_consistent_without_resealing(tmp_path):
    sample(tmp_path)
    assert assess(tmp_path)['status']=='consistent'

def test_stale_pins_block(tmp_path):
    sample(tmp_path); (tmp_path/'evidence.txt').write_text('altered')
    result=assess(tmp_path)
    assert result['status']=='blocked' and any(x['kind']=='stale-pin' for x in result['issues'])

def test_missing_evidence_blocks(tmp_path):
    sample(tmp_path); (tmp_path/'evidence.txt').unlink()
    assert assess(tmp_path)['status']=='blocked'

def test_escape_outside_repository_blocks(tmp_path):
    doc=sample(tmp_path)
    doc['rows'][0]['evidence']=['../outside.txt']
    doc['rows'][0]['evidence_sha256']={'../outside.txt':'a'*64}
    (tmp_path/'dispositions.json').write_text(json.dumps(doc))
    assert any(x['kind']=='missing-or-escaped-evidence' for x in assess(tmp_path)['issues'])

def test_incomplete_bindings_block(tmp_path):
    doc=sample(tmp_path);doc['rows'][0]['evidence_sha256']={}
    (tmp_path/'dispositions.json').write_text(json.dumps(doc))
    assert assess(tmp_path)['status']=='blocked'

def test_missing_attestation_blocks(tmp_path):
    sample(tmp_path);(tmp_path/'attestation.json').unlink()
    assert assess(tmp_path)['status']=='blocked'

def test_preflight_never_mutates_evidence(tmp_path):
    sample(tmp_path);(tmp_path/'evidence.txt').write_text('drift')
    before={p.name:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert assess(tmp_path)['status']=='blocked'
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
