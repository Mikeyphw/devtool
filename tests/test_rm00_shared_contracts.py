"""RM-00: provider/host/evidence contracts remain read-only and fail-closed."""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from devtool.actions_language_providers import PROVIDERS
from devtool.contracts_v2 import (
    CONTRACT_VERSION, ContractError, ProviderDescriptor, reviewed_providers,
    resolve_provider, negotiate_contract, host_eligibility_v2, read_evidence_v2,
)
from devtool.evidence_kernel import graph_host_admission
from devtool.models import WorkflowStepRef, WorkflowConfig, JobConfig, TargetConfig
from devtool.workflow_graph import graph_from_workflow
from test_next02_evidence_kernel import receipt


def test_registry_is_immutable_source_reviewed_and_compatible():
    assert CONTRACT_VERSION == 2
    # RM00 records the source-reviewed registry, not its original two-provider snapshot.
    expected = {'go', 'python-shell', 'rust-cargo', 'node', 'cpp', 'jvm', 'shell-ci', 'dotnet'}
    assert set(PROVIDERS) == set(reviewed_providers()) == expected
    assert {item.provider_id for item in reviewed_providers().values()} == expected
    assert len({id(provider) for provider in PROVIDERS.values()}) == len(expected)
    for provider_id, adapter in PROVIDERS.items():
        descriptor = resolve_provider(provider_id, api_version=adapter.api_version)
        assert descriptor.provider_id == provider_id
        assert descriptor.argv_grammar and descriptor.capabilities and descriptor.host_profiles
        assert descriptor.api_version == 1  # V1 executors retained; RM-00 only adds shared V2 schema
    with pytest.raises(TypeError):
        PROVIDERS['unreviewed'] = object()
    with pytest.raises(TypeError):
        reviewed_providers()['unreviewed'] = object()
    with pytest.raises(ContractError, match='unreviewed'):
        resolve_provider('unreviewed')
    with pytest.raises(ContractError, match='version'):
        resolve_provider('go', api_version=2)
    # Provider admission must not use prefix matching or reflect arbitrary Python modules.
    for unknown in ('Go', 'go-evil', 'dotnet-any', 'python-shell.extra', '__import__', 'missing'):
        with pytest.raises(ContractError):
            resolve_provider(unknown)
    with pytest.raises(TypeError):
        reviewed_providers()['fake-plugin'] = resolve_provider('go')


@pytest.mark.parametrize('bad', ['Bad', '../oops', 'a b', '', 'a'*65])
def test_malformed_provider_identity_rejected(bad):
    with pytest.raises(ContractError):
        ProviderDescriptor(bad, 1, ('build',), ('ubuntu-24.04',), 'direct', ('source-only',))


def test_contract_negotiation_is_bounded_and_feature_explicit():
    selected = negotiate_contract('evidence', [1, 2], required_features=['domain-separation'])
    assert selected['selected_version'] == 2 and not selected['execution_performed']
    assert negotiate_contract('evidence', [1])['selected_version'] == 1
    with pytest.raises(ContractError):
        negotiate_contract('evidence', [1], required_features=['domain-separation'])
    with pytest.raises(ContractError):
        negotiate_contract('provider-registry', [3])
    with pytest.raises(ContractError):
        negotiate_contract('evidence', [True])
    with pytest.raises(ContractError):
        negotiate_contract('evidence', [2], required_features=['unknown-superpower'])


def test_host_eligibility_refuses_missing_host_facts_and_authorities():
    admitted = host_eligibility_v2({'os':'linux','arch':'x86_64',
        'toolchains':['go'], 'capabilities':['readonly'], 'environment':{'CI':'yes'}},
        host_os='linux', host_arch='x86_64', toolchains=('go',),
        capabilities=('readonly',), environment={'CI':'yes'})
    assert admitted['eligible'] and not admitted['trusted_host_verified']
    for requirements, expected in [
        ({'root':True}, 'root authority'), ({'device':True}, 'device authority'),
        ({'signing':True}, 'signing authority'), ({'arch':'aarch64'}, 'arch requirement'),
        ({'toolchains':['dotnet']}, 'missing toolchain'),
        ({'environment':{'RELEASE':'yes'}}, 'environment requirement'),
    ]:
        result = host_eligibility_v2(requirements, host_os='linux', host_arch='x86_64', toolchains=('go',))
        assert not result['eligible'] and any(expected in reason for reason in result['reasons'])


@pytest.mark.parametrize('requirement', [
    {'unknown': True}, {'os':'android'}, {'toolchains':'go'},
    {'toolchains':['go','go']}, {'root':'false'},
    {'environment':{'VALUE':'$(bad)'}}, {'capabilities':[{}]},
])
def test_unsafe_host_declarations_refused(requirement):
    with pytest.raises(ContractError):
        host_eligibility_v2(requirement, host_os='linux', host_arch='x86_64', toolchains=('go',))


def test_v1_receipts_are_readable_without_trust_promotion():
    old = receipt()
    old['gates']['release']['state'] = 'PASS'
    result = read_evidence_v2(old, expected={'source.commit':'a'*40, 'execution.attempt':2})
    assert result['source_schema_version'] == 1
    assert result['reported_gates']['release']['state'] == 'PASS'
    assert all(row['state']=='UNKNOWN' for row in result['effective_domains'].values())
    assert not result['trusted_producer_verified'] and not result['release_qualified']


def test_v2_envelope_is_validated_but_never_promotes_reported_pass():
    old = receipt()
    old['gates']['release']['state'] = 'PASS'
    domains = {name: {'state':'PASS', 'authority':'claimed-signature'}
               for name in ('execution', 'verification', 'device', 'release')}
    envelope = {'schema_version':2,'kind':'devtool.typed-evidence-v2',
                'legacy_evidence':old,'domains':domains}
    result = read_evidence_v2(envelope)
    assert result['source_schema_version'] == 2
    assert all(x['state']=='UNKNOWN' for x in result['effective_domains'].values())
    assert not result['device_qualified'] and not result['release_qualified']
    with pytest.raises(ValueError, match='mismatch'):
        read_evidence_v2(envelope, expected={'source.tree':'f'*40})
    envelope['domains']['release']['state']='FABRICATED'
    with pytest.raises(ContractError):
        read_evidence_v2(envelope)


def test_graph_host_admission_applies_typed_requirements_to_real_dag(tmp_path):
    # Resolve a real native job graph, not a fabricated node projection.
    from devtool.config import load_wrapper_project_config
    p = tmp_path / '.devtool.toml'
    p.write_text('''[project]\nrunner="python"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="python"\nroot="."\n[targets.app.jobs.test]\nrunner="python"\ncommand=["python3","-m","unittest"]\n[targets.app.jobs.test.metadata.ci_host_requirements]\nos="linux"\narch="x86_64"\ntoolchains=["python-shell"]\n[targets.app.workflows]\nci=["job:test"]\n''')
    cfg=load_wrapper_project_config(str(tmp_path),None)
    graph=graph_from_workflow('app',cfg.targets['app'].workflows['ci'])
    good=graph_host_admission(cfg,graph,host_os='linux',host_arch='x86_64',toolchains=('python-shell',))
    assert good['all_eligible'] and good['nodes'][0]['eligibility']['typed_requirements_present']
    p.write_text(p.read_text().replace('toolchains=["python-shell"]','toolchains=["dotnet"]'))
    cfg=load_wrapper_project_config(str(tmp_path),None)
    graph=graph_from_workflow('app',cfg.targets['app'].workflows['ci'])
    refused=graph_host_admission(cfg,graph,host_os='linux',host_arch='x86_64',toolchains=('python-shell',))
    assert not refused['all_eligible']
    assert 'missing toolchain: dotnet' in refused['nodes'][0]['eligibility']['reasons']
