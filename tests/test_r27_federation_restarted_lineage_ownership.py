import pytest
from devtool.core.events import EventBus, RunStarted
from devtool.core.execution_store import ExecutionTransitionError

def start(bus, lineage=None):
    bus.publish(RunStarted(run_id='child-1',command='build',source='federation',project='/repo',
        metadata={'run_lineage':lineage} if lineage is not None else {}))

def test_followup_wrapper_invocation_without_lineage_preserves_authority():
    bus=EventBus()
    start(bus,{'parent_run_id':'parent-A','repository':'child','child_run_id':'child-1','contract_ref':'child::job:lint'})
    start(bus)
    assert bus.execution_store.snapshot('child-1').run_lineage['parent_run_id']=='parent-A'

@pytest.mark.parametrize('change',[
    {'parent_run_id':'parent-B'}, {'repository':'other'},
    {'contract_ref':'child::job:other'}, {'child_run_id':'other-run'},
    {'parent_run_id':'child-1'},
])
def test_repeat_run_started_cannot_change_federation_parent(change):
    bus=EventBus();start(bus,{'parent_run_id':'parent-A','repository':'child',
                               'child_run_id':'child-1','contract_ref':'child::job:lint'})
    with pytest.raises(ExecutionTransitionError):
        start(bus, {'parent_run_id':'parent-A','repository':'child','child_run_id':'child-1',
                    'contract_ref':'child::job:lint',**change})
    assert bus.execution_store.snapshot('child-1').run_lineage['parent_run_id']=='parent-A'

def test_consistent_repeated_lineage_may_add_non_authority_metadata():
    bus=EventBus();start(bus,{'parent_run_id':'parent-A','repository':'child'})
    start(bus,{'parent_run_id':'parent-A','repository':'child','federation_step':'build'})
    snap=bus.execution_store.snapshot('child-1')
    assert snap.run_lineage['parent_run_id']=='parent-A'
    assert snap.run_lineage['federation_step']=='build'
