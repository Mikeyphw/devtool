from dataclasses import replace
from devtool.android.advanced_execution import DeviceCapacity,build_advanced_instrumentation_plan,matrix_evidence
from devtool.android.execution_snapshot import DeviceLease
from devtool.android.instrumentation import InstrumentationRequest,instrumentation_intent
from pathlib import Path

def plan():
    from test_android_x04_advanced_execution import _intent,_lease
    return build_advanced_instrumentation_plan(_intent(),device_capacities=(DeviceCapacity(_lease("USB-1")),),shard_count=2,workers=1)

def good(p):
    return [{"child_id": c.child_id,"ok":True,"receipt_id": f"receipt-{n}","final_child_run_id":f"run-{n}","device_lease_id":c.device_lease_id,"serial":c.serial,"profile_id":c.profile_id} for n,c in enumerate(p.children)]

def test_complete_only_when_every_child_is_bound():
    p=plan();assert matrix_evidence(p,good(p))["complete"] is True

def test_missing_receipt_is_not_complete():
    p=plan();rows=good(p);rows[0]["receipt_id"]="";assert not matrix_evidence(p,rows)["complete"]

def test_failure_is_not_complete():
    p=plan();rows=good(p);rows[0]["ok"]=False;assert not matrix_evidence(p,rows)["complete"]

def test_duplicate_child_cannot_replace_missing_receipt():
    p=plan();rows=good(p);rows.append(dict(rows[0]));result=matrix_evidence(p,rows);assert not result["complete"] and result["duplicate_children"]

def test_duplicate_receipts_not_complete():
    p=plan();rows=good(p);rows[1]["receipt_id"]=rows[0]["receipt_id"];assert not matrix_evidence(p,rows)["complete"]

def test_wrong_lease_or_serial_not_complete():
    p=plan()
    for field in ("device_lease_id","serial","profile_id","final_child_run_id"):
        rows=good(p);rows[0][field]="" if field=="final_child_run_id" else "forged"
        assert not matrix_evidence(p,rows)["complete"],field
