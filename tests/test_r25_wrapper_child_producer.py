from pathlib import Path
from devtool.core.events import EventBus, RunStarted, RunFinished
from devtool.federation_bridge import execute_federated_wrapper
from test_frx07_wrapper_tlj_federation_bridge import _fixture, _load

def test_direct_wrapper_persists_federation_lineage_before_reentry(tmp_path):
    root,_=_fixture(tmp_path);parent=_load(root);bus=EventBus();at_reentry=[]
    def reenter(argv):
        run_id=__import__('os').environ['DEVTOOL_RUN_ID']
        observed=bus.execution_store.snapshot(run_id)
        assert observed is not None
        assert observed.run_lineage['child_run_id']==run_id
        assert observed.run_lineage['relationship']=='child'
        at_reentry.append(observed.run_lineage['parent_run_id'])
        # A wrapper that reenters without handoff should not erase the already
        # persisted authoritative federation lineage.
        bus.publish(RunStarted(run_id=run_id,source='wrapper', command='build',
                               project=str(tmp_path),metadata={'invocation_source':'wrapper'}))
        bus.publish(RunFinished(run_id=run_id, status='succeeded',exit_code=0))
        return 0
    child=execute_federated_wrapper(parent,'child',['build'],reenter=reenter,
        publish=bus.publish,snapshot_lookup=bus.execution_store.snapshot)
    assert child.status=='succeeded' and child.exit_code==0
    assert at_reentry==[child.run_lineage['parent_run_id']]
    assert bus.execution_store.snapshot(child.run_id).run_lineage['parent_run_id']==at_reentry[0]
