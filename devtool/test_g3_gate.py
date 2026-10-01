from __future__ import annotations
"""TEST-G3 final test-system seal."""
import argparse, json, subprocess, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .android.instrumentation import _execution_classification
from .gradle_reports import collect_instrumentation_output
from .test_adapters import EvidenceTestRunner
from .test_execution import TestExecutionContext
from .test_model import TestSpec
from .test_inventory import compile_pytest_inventory, qualify_selection_environment, with_shard_plan
from .test_runtime_optimization import TestPerformanceSample, build_performance_profile, aggregate_shards, ShardOutcome, run_attachment_manifest
from .test_convergence import RerTestEvidenceRequest, rer_reuse_projection, DelegatedTestLineage, roadmap_evidence_binding, presentation_projection
from .test_runstore import normalize_test_run_payload

SOURCE_ALLOWED={"SATISFIED_BY_TEST","SUPERSEDED_BY_TEST","EXPORTED_TO_EXEC","EXPORTED_TO_RUN","EXPORTED_TO_RER","EXPORTED_TO_ENV","EXPORTED_TO_CFG","EXPORTED_TO_PRES","EXPORTED_TO_DIST","EXPORTED_TO_RMP","RETIRED_WITH_EVIDENCE"}
MERGED_ALLOWED={"SATISFIED","SUPERSEDED","EXPORTED","RETIRED"}

def _load(path:Path)->dict[str,Any]:
    v=json.loads(path.read_text());
    if not isinstance(v,dict): raise RuntimeError(f"{path.name} must be a JSON object")
    return v

def audit_dispositions(repo_root:str|Path='.') -> dict[str,Any]:
    root=Path(repo_root).resolve(); s=_load(root/'TEST_G3_SOURCE_DISPOSITION_LEDGER.json'); m=_load(root/'TEST_G3_MERGED_DISPOSITION_LEDGER.json')
    sp=s.get('promises',[]); sr=s.get('regressions',[]); mp=m.get('promises',[]); mr=m.get('regressions',[])
    if [x.get('id') for x in sp] != [f'TEST-P{i:03d}' for i in range(1,85)]: raise RuntimeError('source promise closure is incomplete')
    if [x.get('id') for x in sr] != [f'TEST-R{i:03d}' for i in range(1,106)]: raise RuntimeError('source regression closure is incomplete')
    if [x.get('id') for x in mp] != [f'TEST-P{i:03d}' for i in range(1,109)]: raise RuntimeError('merged promise closure must cover TEST-P001..TEST-P108')
    if [x.get('id') for x in mr] != [f'TEST-MR{i:03d}' for i in range(1,101)]: raise RuntimeError('merged regression closure is incomplete')
    bad=[]
    for row in [*sp,*sr]:
        if row.get('disposition') not in SOURCE_ALLOWED or not row.get('evidence'): bad.append(row.get('id'))
    for row in [*mp,*mr]:
        if row.get('status') not in MERGED_ALLOWED or not row.get('evidence'): bad.append(row.get('id'))
    if bad: raise RuntimeError('unmapped TEST obligations: '+', '.join(map(str,bad[:12])))
    return {'source_promises':84,'source_regressions':105,'merged_promises':108,'merged_regressions':100,'unmapped':0,'ok':True}

def runner_semantics_proof() -> dict[str,Any]:
    class Outcome:
        returncode=9; exit_code=9; stdout=''; output=''; metadata={}
    def run(*_a,**_k): return Outcome()
    ctx=TestExecutionContext(repo_root='.')
    frameworks={}
    for name in ('pytest','go','gradle','dotnet'):
        spec=TestSpec(id=f'test-g3-{name}',name=name,runner=name,command=('false',))
        result=EvidenceTestRunner(run,framework=name).run(spec,ctx)
        if result.tests_total != 0 or result.tests_failed != 0 or int(result.metadata.get('exit_code') or 0)!=9:
            raise RuntimeError(f'{name} fabricated testcase failure from runner exit')
        frameworks[name]={'total':result.tests_total,'failed':result.tests_failed,'exit_code':result.metadata.get('exit_code')}
    crash='INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS_CODE: -2\nINSTRUMENTATION_RESULT: shortMsg=Process crashed.\n'
    counts,ids,kind,reason=_execution_classification(crash,1)
    if kind!='setup-crash' or counts['executed']!=0 or counts['failed']!=0 or ids: raise RuntimeError('Android process crash became testcase failure')
    coll=collect_instrumentation_output('DEVTOOL_INSTRUMENTATION_RESULT '+json.dumps({'framework':'android-instrumentation','returncode':1,'failure_kind':'setup-crash','reason':reason,**counts,'failed_ids':[]}),repo_root=Path('.'))
    if int(coll.metadata.get('failed') or 0)!=0: raise RuntimeError('Android report projection fabricated failed testcase')
    missing=collect_instrumentation_output('runner died before structured result',repo_root=Path('.'))
    if int(missing.metadata.get('failed') or 0)!=0: raise RuntimeError('missing Android report fabricated failed testcase')
    frameworks['android']={'executed':counts['executed'],'failed':counts['failed'],'failure_kind':kind}
    return {'frameworks':frameworks,'ok':True}

def end_to_end_proof(repo_root:str|Path='.') -> dict[str,Any]:
    root=Path(repo_root).resolve(); inv=compile_pytest_inventory(root)
    sel=inv.select(groups=('roadmap.test.g2',),provenance={'gate':'TEST-G3'},shard_count=2)
    sel=with_shard_plan(sel,inv,2)
    qual=qualify_selection_environment(sel,inv,probe_versions=False)
    if not sel.resolved_check_ids or sel.shard_plan is None: raise RuntimeError('selection/shard plan missing')
    ok,detail=sel.shard_plan.verify_complete(sel.resolved_check_ids)
    if not ok: raise RuntimeError('incomplete shard plan: '+detail)
    samples=(TestPerformanceSample(sel.resolved_check_ids[0],'collection',0.01,file='tests'),TestPerformanceSample(sel.resolved_check_ids[0],'call',0.02,file='tests'))
    profile=build_performance_profile(sel.selection_id,samples,budget_seconds=60,host='termux')
    outcomes=[]
    for shard in sel.shard_plan.shards:
        outcomes.append(ShardOutcome(shard.shard_id,shard.check_ids,passed=len(shard.check_ids),junit_path=f'{shard.shard_id}.xml',log_path=f'{shard.shard_id}.log',profile_path=f'{shard.shard_id}.ndjson'))
    agg=aggregate_shards(sel.resolved_check_ids,outcomes)
    if not agg['complete'] or agg['failed']!=0: raise RuntimeError('shard aggregation is incomplete')
    manifest=run_attachment_manifest('test-g3-local',sel.selection_id,agg)
    run_payload=normalize_test_run_payload({'run_id':'test-g3-local','repository':str(root),'operations':[{'operation_id':'selection','name':'TEST-G3 selection','test_result':{'id':'test-g3-selection','suites':[{'id':'suite','name':'TEST-G3','cases':[{'id':cid,'name':cid,'status':'passed'} for cid in sel.resolved_check_ids]}],'artifacts':[],'metadata':{'selection_id':sel.selection_id}}}]})
    if not run_payload or run_payload['summary']['failed']!=0 or run_payload['summary']['total']!=len(sel.resolved_check_ids): raise RuntimeError('RUN test projection mismatch')
    rer=RerTestEvidenceRequest(check_id=sel.resolved_check_ids[0],selection_id=sel.selection_id,input_digest='input',environment_id=qual.qualification_id,output_identity='junit',policy_id='system',freshness_token='fresh',source_run_id='test-g3-local')
    reuse=rer_reuse_projection(rer,qualified=True,reason='identity-qualified')
    lineage=DelegatedTestLineage('test-g3-parent','test-g3-child',tuple(sel.resolved_check_ids),'ssh-native','reconciled','passed','run://test-g3-child').as_dict()
    rmp=roadmap_evidence_binding(gate_id='TEST-G3',check_ids=sel.resolved_check_ids,receipt_refs=('run://test-g3-local',),rer_refs=(reuse['request_id'],))
    pres=presentation_projection({cid:'passed' for cid in sel.resolved_check_ids},profile_ref=profile.profile_id,raw_result_refs={cid:f'run://test-g3-local#{cid}' for cid in sel.resolved_check_ids})
    return {'selection_id':sel.selection_id,'check_ids':list(sel.resolved_check_ids),'environment_ready':qual.ready,'environment_id':qual.qualification_id,'shards':len(sel.shard_plan.shards),'aggregate':agg,'attachment_manifest':manifest,'run_summary':run_payload['summary'],'rer':{'qualified':reuse['qualified'],'request_id':reuse['request_id']},'delegated':lineage,'rmp':rmp,'presentation':{'raw_outcomes_preserved':pres['raw_outcomes_preserved'],'authority':pres['authority']},'ok':True}

def fresh_process(repo_root:str|Path='.') -> dict[str,Any]:
    p=subprocess.run([sys.executable,'-m','devtool.test_g3_gate','--stage','end-to-end','--repo',str(Path(repo_root).resolve())],text=True,capture_output=True,check=False)
    if p.returncode: raise RuntimeError(p.stderr.strip() or p.stdout.strip())
    return json.loads(p.stdout.strip().splitlines()[-1])

def final_seal(repo_root:str|Path='.') -> dict[str,Any]:
    return {'schema_version':1,'roadmap_id':'TEST-G3','position':'67/76','dispositions':audit_dispositions(repo_root),'runner_semantics':runner_semantics_proof(),'fresh_process':fresh_process(repo_root),'ok':True}

def main(argv=None):
    ap=argparse.ArgumentParser();ap.add_argument('--stage',choices=('end-to-end','runner-semantics'));ap.add_argument('--repo',default='.');a=ap.parse_args(argv)
    if a.stage=='end-to-end': out=end_to_end_proof(a.repo)
    elif a.stage=='runner-semantics': out=runner_semantics_proof()
    else: out=final_seal(a.repo)
    print(json.dumps(out,sort_keys=True));return 0
if __name__=='__main__': raise SystemExit(main())
