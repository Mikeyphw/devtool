from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
from devtool.test_g3_gate import audit_dispositions, runner_semantics_proof, end_to_end_proof, fresh_process
from devtool import android_instrumentation
from devtool.gradle_reports import collect_instrumentation_output

def test_test_g3_source_and_merged_closure_is_complete():
    assert audit_dispositions(Path.cwd()) == {'source_promises':84,'source_regressions':105,'merged_promises':108,'merged_regressions':100,'unmapped':0,'ok':True}

def test_test_g3_runner_failure_never_fabricates_testcase_failure():
    p=runner_semantics_proof(); assert p['ok']; assert all(v.get('failed',0)==0 for v in p['frameworks'].values())

def test_test_g3_android_legacy_compatibility_process_crash_is_setup_failure():
    crash='INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS_CODE: -2\nINSTRUMENTATION_RESULT: shortMsg=Process crashed.\n'
    from devtool.android.instrumentation import _execution_classification
    c,ids,k,_=_execution_classification(crash,1); assert (k,c['executed'],c['failed'],ids)==('setup-crash',0,0,[])

def test_test_g3_missing_android_structured_result_is_runner_error_not_failed_test():
    c=collect_instrumentation_output('runner died',repo_root=Path.cwd()); assert c.metadata['failed']==0; assert c.issues and c.issues[0].severity=='error'

def test_test_g3_end_to_end_selection_environment_run_rer_dist_pres_rmp_chain():
    p=end_to_end_proof(Path.cwd()); assert p['ok']; assert p['aggregate']['complete']; assert p['run_summary']['failed']==0; assert p['rer']['qualified']; assert p['delegated']['transport_overwrites_test_truth'] is False; assert p['presentation']['raw_outcomes_preserved']; assert not p['rmp']['pytest_paths']

def test_test_g3_fresh_process_chain():
    assert fresh_process(Path.cwd())['ok'] is True

def test_test_g3_module_cli_final_seal():
    p=subprocess.run([sys.executable,'-m','devtool.test_g3_gate'],text=True,capture_output=True,check=False); assert p.returncode==0,p.stderr; j=json.loads(p.stdout.strip().splitlines()[-1]); assert j['roadmap_id']=='TEST-G3' and j['position']=='67/76' and j['ok']
