"""Independently check disposition structure, ownership and named obligations.

These are current *source/ledger* obligations, not Android device/ release proof.
"""
from __future__ import annotations
import json
from pathlib import Path

PRESENTATION_SURFACES={'machine','terminal','tui','desktop','android_hud','tasker','ipc','web','bridge'}
PRESENTATION_INVARIANTS={'diagnostics','actions','notifications','bridge','stream'}

def evaluate(root:Path)->dict:
 root=Path(root).resolve()
 def load(filename):return json.loads((root/filename).read_bytes())
 android=load('ANDROID_G3_SOURCE_DISPOSITIONS.json');asrc=android['rows']
 ls=load('LIFE_G3_SOURCE_DISPOSITIONS.json'); lsr=ls['rows']
 lf=load('LIFE_G3_FEATURE_DISPOSITIONS.json');lfr=lf['rows']
 lp=load('LIFE_G3_PROMISE_DISPOSITIONS.json');lpr=lp['rows']
 matrix=load('REMED_R04B_PRESENTATION_SURFACE_MATRIX.json');mr=matrix['rows']
 expected_android={f'AND-P{i:03d}' for i in range(1,85)}|{f'INST-P{i:03d}' for i in range(1,85)}
 expected_promises={f'LIFE-P{i:03d}' for i in range(1,118)}|{f'LIFE-P{i:03d}' for i in range(123,128)}
 reserved={f'LIFE-P{i:03d}' for i in range(118,123)}
 expected_features={f'FEA-X{i:02d}' for i in range(7)}|{f'FEA-R{i:02d}' for i in range(1,39)}
 checks={
  'ANDROID_168_unique_named_source_obligations':android.get('source_obligations')==168 and len(asrc)==168 and {r.get('source_id') for r in asrc}==expected_android,
  'ANDROID_qualified_disposition_types':all(r.get('disposition') in {'SATISFIED','SUPERSEDED','EXPORTED','RETIRED'} for r in asrc),
  'ANDROID_exported_owner_contract_present':all(r.get('disposition')!='EXPORTED' or (r.get('export_owner') and r.get('export_contract')) for r in asrc),
  'ANDROID_source_row_disposition_rationale_present':all(r.get('source_text') and r.get('closure_reason') and r.get('mapping_basis') for r in asrc),
  'LIFE_168_source_obligations':ls.get('source_obligations')==168 and len(lsr)==168 and len({r.get('source_id') for r in lsr})==168,
  'LIFE_45_named_feature_obligations':lf.get('feature_obligations')==45 and len(lfr)==45 and {r.get('obligation_id') for r in lfr}==expected_features,
  'LIFE_122_promises_and_reserved_ids':lp.get('defined_life_promises')==122 and len(lpr)==122 and {r.get('promise_id') for r in lpr}==expected_promises and not (reserved & {r.get('promise_id') for r in lpr}),
  'LIFE_feature_evidence_and_reasons':all(r.get('source_text') and r.get('closure_reason') and r.get('evidence') for r in lfr),
  'LIFE_promise_evidence_and_reasons':all(r.get('promise_text') and r.get('closure_reason') and r.get('evidence') for r in lpr),
  'R04B_9_presentation_surfaces':PRESENTATION_SURFACES<={r.get('surface') for r in mr},
  'R04B_5_presentation_invariants':PRESENTATION_INVARIANTS<={r.get('invariant') for r in mr},
  'R04B_presentation_rows_have_evidence':all(r.get('authority') and r.get('evidence') and set(r['evidence'])==set(r.get('evidence_sha256',{})) for r in mr),
 }
 return {'schema_version':2,'scope':'LEDGER_STRUCTURE_AND_SOURCE_AUTHORITY_ONLY',
 'checks':{k:bool(v) for k,v in checks.items()},
 'passed':sum(bool(v) for v in checks.values()),'total':len(checks),
 'result':'PASS_CURRENT_LEDGER_STRUCTURE' if all(checks.values()) else 'FAIL_CURRENT_LEDGER_STRUCTURE',
 'release_qualified':False,'native_device_qualified':False}
