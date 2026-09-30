from __future__ import annotations
import pytest
from devtool.textual_control_center import PAGES, NAV_KEYS, TextualControlCenter, capabilities, replay, assert_reference_only


def test_control_center_has_complete_operator_pages_and_stable_keys():
    assert set(PAGES) == {"dashboard","runs","logs","diagnostics","history","targets","workers","validation","artifacts","evidence"}
    assert NAV_KEYS["l"] == "logs" and NAV_KEYS["w"] == "workers" and NAV_KEYS["s"] == "artifacts"
    assert len(NAV_KEYS) == len(set(NAV_KEYS))


def test_progress_is_coalesced_but_terminal_transition_is_preserved():
    c=TextualControlCenter(max_events=20)
    for i in range(100): c.ingest({"kind":"progress","run_id":"r1","node_id":"n1","status":"running","percent":i})
    c.ingest({"kind":"progress","run_id":"r1","node_id":"n1","status":"failed","percent":99})
    assert len(c.events) == 2
    assert c.events[-1]["status"] == "failed"
    assert c.dropped_progress_updates == 99


def test_logs_are_bounded_and_secrets_do_not_enter_normal_stream():
    c=TextualControlCenter(max_log_lines=3)
    for i in range(5): c.ingest({"kind":"stdout","text":str(i),"token":"nope"})
    assert [x["text"] for x in c.logs] == ["2","3","4"]
    assert all("token" not in x for x in c.logs)


def test_runs_attention_and_hierarchy_are_canonical_projection_only():
    snap=replay([
        {"kind":"node","run_id":"b","node_id":"child","state":"running"},
        {"kind":"node","run_id":"a","node_id":"root","state":"passed"},
        {"kind":"node","run_id":"b","node_id":"child2","state":"failed"},
    ])
    assert snap["runs"][0]["run_id"] == "b"
    assert snap["runs"][0]["nodes"] == ["child","child2"]
    assert snap["attention"][0]["state"] == "failed"
    assert_reference_only(snap)


def test_actions_are_requests_not_success_and_use_domain_authority():
    c=TextualControlCenter(); c.select(run_id="run-1",node_id="node-2")
    retry=c.action_request("retry")
    assert retry.status == "requested" and retry.authority == "EXO/RUN"
    with pytest.raises(PermissionError): c.action_request("artifact.apply")
    apply=c.action_request("artifact.apply",confirmed=True)
    assert apply.confirmation_required and apply.authority == "LIFE"


def test_responsive_accessible_capabilities_are_truthful():
    wide=capabilities(width=140,height=40,clipboard_available=True)
    assert wide.mode == "wide" and wide.multi_pane and wide.tree and wide.clipboard
    narrow=capabilities(width=45,height=20)
    assert narrow.mode == "ultra-narrow" and not narrow.multi_pane
    plain=capabilities(width=100,height=30,term="dumb")
    assert plain.mode == "plain" and not plain.mouse and not plain.hyperlink and not plain.resize
    sr=capabilities(width=100,height=30,screen_reader=True)
    assert sr.mode == "plain" and sr.screen_reader


def test_snapshot_pause_follow_and_bounded_projection():
    c=TextualControlCenter(max_log_lines=100)
    for i in range(700): c.ingest({"kind":"log","text":f"line-{i}"})
    snap=c.snapshot(); assert len(snap["log_lines"]) == 100
    c.paused=True; assert c.snapshot()["log_lines"] == []
    c.follow_logs=False; assert c.snapshot()["follow_logs"] is False


def test_replay_is_deterministic_for_same_semantic_records():
    records=[{"kind":"node","run_id":"r","node_id":"n","state":"running"},{"kind":"node","run_id":"r","node_id":"n","state":"passed"}]
    assert replay(records)["snapshot_digest"] == replay(records)["snapshot_digest"]
