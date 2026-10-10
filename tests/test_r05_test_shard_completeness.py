from __future__ import annotations

import pytest
from devtool.test_runtime_optimization import ShardOutcome, aggregate_shards, run_attachment_manifest


def test_complete_counts_are_terminal_and_consistent():
    aggregate=aggregate_shards(("a", "b"), (ShardOutcome("s1", ("a",),passed=1), ShardOutcome("s2", ("b",),skipped=1)))
    assert aggregate["complete"]
    assert run_attachment_manifest("run", "selection", aggregate)["complete"]


@pytest.mark.parametrize("records", [
    [ShardOutcome("s1", ("a",),passed=0)],
    [ShardOutcome("s1", ("a",),passed=2)],
    [ShardOutcome("s1", ("a",),failed=-1,passed=2)],
    [ShardOutcome("s1", ("a",),passed=1),ShardOutcome("s1", ("b",),passed=1)],
    [ShardOutcome("", ("a",),passed=1),ShardOutcome("s2", ("b",),passed=1)],
])
def test_shard_count_and_identity_must_agree(records):
    expected=("a", "b") if len(records)==2 else ("a",)
    aggregated=aggregate_shards(expected, records)
    assert not aggregated["complete"]
    assert not run_attachment_manifest("run", "selection", aggregated)["complete"]


def test_attacker_supplied_aggregate_cannot_force_manifest_complete():
    forged={"complete":True,"passed":0,"failed":0,"skipped":0,"expected_check_count":2,"observed_check_count":2,"attachments":[{"shard_id":"s1"}]}
    assert not run_attachment_manifest("run", "selection", forged)["complete"]
    forged.update({"passed":2,"attachments":[{"shard_id":"s1"},{"shard_id":"s1"}]})
    assert not run_attachment_manifest("run", "selection", forged)["complete"]
