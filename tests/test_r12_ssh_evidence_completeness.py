from __future__ import annotations
import pytest
from devtool.distributed_execution import DelegationLink, ExecutionRunLink
from devtool.distributed_provider_control import (
    ProviderSessionBinding, ProviderSessionBindingError, ssh_provider_event_channel,
    dispatch_ssh_provider_control,
)
from devtool.distributed_transport import TransportProtocolCapabilities
from devtool.ssh_tmux_runner import SSHRunnerConfig


def link():
    return DelegationLink(
        run_link=ExecutionRunLink("parent-run", "parent-node", "child-run"),
        repository="backend", target="app", child_contract="workflow:check",
        contract_ref="backend::app::workflow:check", federation_ref="backend::app::check",
        repository_fingerprint="repo-fingerprint", source_lock_digest="source-lock",
        source_equivalence_digest="source-equivalence",
        provider_identity={"provider_id":"tablet", "provider_kind":"ssh", "profile":"tablet",
                           "execution_environment":"host", "provider_evidence_digest":"provider-evidence"},
        attestation_digest="attestation",
    )

def context():
    cfg=SSHRunnerConfig(host="tablet", user="termux", profile="tablet")
    candidate=link()
    binding=ProviderSessionBinding.bind(candidate,session_id="session-1",profile=cfg.profile,
        destination=cfg.destination,provider_kind="ssh",provider_evidence_digest="provider-evidence",
        attestation_digest=candidate.attestation_digest)
    observation={"child_run_id":candidate.child_run_id,"delegation_id":candidate.delegation_id,
        "session":"session-1","provider_session_binding_id":binding.binding_id,
        "journal":'{"event_type":"run.started","metadata":{"dist_transport_peer":"ssh-remote-devtool","dist_transport_capabilities":'+__import__('json').dumps(TransportProtocolCapabilities().as_dict())+'}}\n'}
    return cfg,candidate,observation

@pytest.mark.parametrize("missing", ["child_run_id", "delegation_id", "provider_session_binding_id"])
def test_incomplete_event_observation_rejected(monkeypatch,tmp_path,missing):
    cfg,candidate,observation=context();observation.pop(missing)
    monkeypatch.setattr("devtool.ssh_resilience.read_remote_run_journal", lambda *_a,**_k:observation)
    with pytest.raises(ProviderSessionBindingError):
        ssh_provider_event_channel(cfg,link=candidate,session="session-1",cursor_path=tmp_path/"cursor")

@pytest.mark.parametrize("missing", ["child_run_id", "delegation_id", "provider_session_binding_id"])
def test_incomplete_control_observation_rejected(monkeypatch,missing):
    cfg,candidate,observation=context();observation.pop(missing)
    monkeypatch.setattr("devtool.ssh_resilience.read_remote_run_journal",lambda *_a,**_k:observation)
    with pytest.raises(ProviderSessionBindingError):
        dispatch_ssh_provider_control(cfg,link=candidate)

def test_complete_event_observation_accepted(monkeypatch,tmp_path):
    cfg,candidate,observation=context()
    monkeypatch.setattr("devtool.ssh_resilience.read_remote_run_journal",lambda *_a,**_k:observation)
    channel=ssh_provider_event_channel(cfg,link=candidate,session="session-1",cursor_path=tmp_path/"cursor")
    assert channel.binding.delegation_id==candidate.delegation_id

def test_complete_control_observation_calls_remote_owner(monkeypatch):
    cfg,candidate,observation=context();calls=[]
    monkeypatch.setattr("devtool.ssh_resilience.read_remote_run_journal",lambda *_a,**_k:observation)
    monkeypatch.setattr("devtool.ssh_resilience.cancel_remote_run",lambda *_a,**_k:calls.append(True) or {"ok":True,"cancelled":[{}]})
    ack=dispatch_ssh_provider_control(cfg,link=candidate)
    assert ack.accepted and calls
