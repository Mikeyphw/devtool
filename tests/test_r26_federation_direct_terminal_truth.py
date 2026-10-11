from types import SimpleNamespace
import pytest
from devtool.federation_execution import _direct_child_terminal_truth

@pytest.mark.parametrize('rc,status,code',[
    (0,'succeeded',0),(8,'failed',8),(130,'cancelled',130),
])
def test_no_snapshot_uses_real_process_code(rc,status,code):
    assert _direct_child_terminal_truth(None,rc)==(status,code)

@pytest.mark.parametrize('snapshot_status,process_code,snapshot_code,expected',[
    ('succeeded',0,0,('succeeded',0)),
    ('succeeded',9,0,('failed',9)),
    ('failed',0,1,('failed',1)),
    ('cancelled',0,130,('cancelled',130)),
    ('running',0,None,('failed',1)),
    ('blocked',0,None,('failed',1)),
    ('skipped',0,None,('failed',1)),
    ('garbage',0,None,('failed',1)),
    ('succeeded',0,None,('failed',1)),
    ('succeeded',0,True,('failed',1)),
    ('succeeded',0,5,('failed',1)),
])
def test_conflicting_or_nonterminal_evidence_fails_closed(snapshot_status,process_code,snapshot_code,expected):
    snap=SimpleNamespace(status=snapshot_status,exit_code=snapshot_code)
    assert _direct_child_terminal_truth(snap,process_code)==expected
