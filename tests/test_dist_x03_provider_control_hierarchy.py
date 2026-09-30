from __future__ import annotations

import json
from pathlib import Path

import pytest

from devtool.core.events import EventBus, NodeDiscovered, RunFinished, RunStarted
from devtool.core.events.listeners import event_to_dict
from devtool.distributed_execution import DelegationLink, ExecutionRunLink, ExternalEventIngestor
from devtool.distributed_provider_control import (
    DelegatedControlChannel,
    DelegatedControlConflictError,
    DelegatedControlRequest,
    DelegationRoute,
    DelegationRouteCycleError,
    PrivateTransportHook,
    ProviderSessionBinding,
    ProviderSessionBindingError,
    provider_cleanup_descriptors,
    record_delegation_relation,
    run_relation_from_delegation,
    ssh_phase_dist_metadata,
    ssh_provider_event_channel,
    typed_tool_argv_evidence,
)
from devtool.distributed_transport import DelegatedEventReceiver
from devtool.federation_operations import request_federated_child_control
from devtool.run_store import AttachmentDescriptor, RunCatalog
from devtool.ssh_tmux_runner import SSHRunnerConfig


def _link(child: str = "child-run", *, parent: str = "parent-run", provider_kind: str = "ssh") -> DelegationLink:
    return DelegationLink(
        run_link=ExecutionRunLink(parent, "parent-node", child),
        repository="backend",
        target="app",
        child_contract="workflow:check",
        contract_ref="backend::app::workflow:check",
        federation_ref="backend::app::check",
        repository_fingerprint="repo-fingerprint",
        source_lock_digest="source-lock",
        source_equivalence_digest="source-equivalence",
        provider_identity={
            "provider_id": "tablet",
            "provider_kind": provider_kind,
            "profile": "tablet" if provider_kind == "ssh" else "",
            "execution_environment": "host",
            "provider_evidence_digest": "provider-evidence",
        },
        attestation_digest="attestation",
    )


def _complete(event, sequence: int):
    event.complete_envelope(sequence=sequence)
    return event


def _journal(events) -> str:
    return "\n".join(json.dumps(event_to_dict(event), sort_keys=True) for event in events) + "\n"


def test_nested_route_rejects_repeated_delegation_or_child_and_hop_overflow() -> None:
    first = _link("child-a")
    route = DelegationRoute(max_hops=2).append(first)
    with pytest.raises(DelegationRouteCycleError):
        route.append(first)
    repeated_child = _link("child-a", parent="different-parent")
    with pytest.raises(DelegationRouteCycleError):
        route.append(repeated_child)
    second = _link("child-b")
    route = route.append(second)
    with pytest.raises(DelegationRouteCycleError, match="maximum hop"):
        route.append(_link("child-c"))
    assert DelegationRoute.from_json(route.to_json()) == route


def test_provider_session_binding_is_attestation_and_provider_bound() -> None:
    link = _link()
    binding = ProviderSessionBinding.bind(
        link,
        session_id="session-1",
        profile="tablet",
        destination="termux@tablet",
        provider_kind="ssh",
        provider_evidence_digest="provider-evidence",
    )
    assert binding.delegation_id == link.delegation_id
    assert binding.attestation_digest == link.attestation_digest
    with pytest.raises(ProviderSessionBindingError):
        ProviderSessionBinding.bind(link, session_id="session-1", provider_kind="oci")


def test_typed_control_cancels_when_telemetry_is_degraded_and_replay_is_idempotent(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "devtool.ipc.request_run_cancel",
        lambda run_id, *, reason: (calls.append((run_id, reason)) or True, "cancel-requested"),
    )
    link = _link()
    ack = request_federated_child_control(
        link.child_run_id,
        delegation_id=link.delegation_id,
        reason="parent interrupted",
        telemetry_health="degraded",
    )
    assert ack.accepted and ack.detail == "cancel-requested"
    assert calls == [(link.child_run_id, "parent interrupted")]

    channel = DelegatedControlChannel()
    request = DelegatedControlRequest.cancel(
        delegation_id=link.delegation_id,
        child_run_id=link.child_run_id,
        reason="again",
    )
    first = channel.dispatch(request, cancel=lambda *_: (True, "ok"), telemetry_health="offline")
    second = channel.dispatch(request, cancel=lambda *_: (_ for _ in ()).throw(AssertionError("replayed")))
    assert second == first
    with pytest.raises(DelegatedControlConflictError):
        DelegatedControlRequest(
            delegation_id=link.delegation_id,
            child_run_id=link.child_run_id,
            action="cancel",
            reason="different",
            request_id=request.request_id,
        )


def test_run_v3_relation_persists_queries_progress_and_pins_descendant_gc(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    link = _link()
    relation = record_delegation_relation(root, link, delivery_health="live")
    catalog = RunCatalog.open(root / ".devtool" / "private" / "run-v3")
    assert catalog.relations[relation.relation_id].delegation_id == link.delegation_id
    catalog.add_run({"run_id": "parent-run", "status": "running"})
    catalog.add_run({"run_id": "child-run", "status": "succeeded"})
    grandchild = run_relation_from_delegation(_link("grandchild", parent="child-run"), delivery_health="reconciled")
    catalog.add_relation(grandchild)
    catalog.pin_relation(relation.relation_id)

    artifact = tmp_path / "child.txt"
    artifact.write_text("child", encoding="utf-8")
    descriptor = AttachmentDescriptor.from_file(run_id="grandchild", path=artifact, kind="evidence")
    catalog.add_attachment(descriptor)
    catalog.tombstone_attachment(descriptor.attachment_id)
    catalog.save()

    reloaded = RunCatalog.open(catalog.root)
    assert [row["run"]["run_id"] for row in reloaded.ancestors("grandchild")] == ["child-run", "parent-run"]
    assert [row["run"]["run_id"] for row in reloaded.descendants("parent-run")] == ["child-run", "grandchild"]
    assert reloaded.subtree("child-run")["root"]["run_id"] == "child-run"
    progress = reloaded.parent_progress("parent-run")
    assert progress["child_count"] == 1 and progress["terminal_count"] == 1
    assert progress["flattened_child_identity"] is False
    assert str(artifact) not in reloaded.gc_plan().delete_paths
    raw = json.loads((catalog.root / "catalog.json").read_text(encoding="utf-8"))
    assert relation.relation_id in raw["relations"]


def test_remote_provider_cleanup_is_destination_local_and_life_owned() -> None:
    rows = provider_cleanup_descriptors(
        destination="termux@tablet",
        provider="ssh",
        run_id="child-run",
        session="session-1",
        stage_path="~/stage/work",
        state_path="~/.cache/devtool/ssh-runs/child/session",
        cursor_path="~/.cache/devtool/ssh-runs/child/session/event-cursor.json",
        delegation_id="delegation",
    )
    assert {row.category for row in rows} == {"provider-staging", "provider-session-state", "event-cursor"}
    assert all(row.scope.value == "remote" for row in rows)
    assert all(row.destination == "termux@tablet" for row in rows)
    assert all(row.owner == "DIST" and row.provenance["authority"] == "RUN/LIFE" for row in rows)
    assert rows[0].path == "~/stage/work"


def test_private_secret_hooks_and_typed_argv_never_shell_reparse() -> None:
    hook = PrivateTransportHook(("secret-ref-1",), ("TOKEN", "PASSWORD"), "ssh")
    payload = hook.as_dict()
    assert payload["contains_secret_values"] is False
    assert "secret-value" not in json.dumps(payload)
    argv = ["tool", "--arg", "two words", "$(not-shell)", "semi;colon"]
    evidence = typed_tool_argv_evidence(argv)
    assert evidence["argv"] == argv
    assert evidence["token_count"] == len(argv)
    assert evidence["shell_reparsed"] is False


def test_ssh_metadata_and_detached_reconnect_use_canonical_run_journal(monkeypatch, tmp_path: Path) -> None:
    link = _link()
    metadata = ssh_phase_dist_metadata(
        link=link,
        child_run_id=link.child_run_id,
        remote_root="~/Code/backend",
        session="session-1",
        profile="tablet",
        destination="termux@tablet",
        provider_kind="ssh",
        provider_evidence_digest="provider-evidence",
        secret_keys=("TOKEN",),
        secret_reference_ids=("secret-ref-1",),
        original_argv=("devtool", "invoke", "--arg", "two words"),
    )
    assert metadata["dist_origin_journal_path"].endswith(
        "/.devtool/private/run-v3/child-run/execution-events.jsonl"
    )
    assert metadata["dist_provider_session_binding_id"]
    assert metadata["dist_private_transport_hook_id"]

    events = [
        _complete(RunStarted(run_id="child-run", command="check"), 1),
        _complete(NodeDiscovered(run_id="child-run", node_id="n", label="compile"), 2),
        _complete(RunFinished(run_id="child-run", status="succeeded", exit_code=0), 3),
    ]
    state = {"journal": _journal(events[:2])}

    def fake_read(config, *, run_id="", session="", node_id=""):
        assert run_id == "child-run" and session == "session-1"
        return {
            "journal": state["journal"],
            "metadata": {"execution_provider": "ssh", "dist_attestation_digest": link.attestation_digest},
            "provider_observation_only": True,
            "semantic_authority": "RUN-V3",
        }

    monkeypatch.setattr("devtool.ssh_resilience.read_remote_run_journal", fake_read)
    cfg = SSHRunnerConfig(host="tablet", user="termux", profile="tablet")
    channel = ssh_provider_event_channel(
        cfg,
        link=link,
        session="session-1",
        cursor_path=tmp_path / "cursor.json",
    )
    bus = EventBus()
    receiver = DelegatedEventReceiver(ExternalEventIngestor(bus), link=link)
    assert [ack.acknowledged_sequence for ack in channel.catch_up(receiver)] == [1, 2]
    state["journal"] = _journal(events)
    assert [frame.sequence for frame in channel.frames_after_ack()] == [3]
    assert [ack.acknowledged_sequence for ack in channel.catch_up(receiver)] == [3]
    assert channel.health().acknowledged_sequence == 3
