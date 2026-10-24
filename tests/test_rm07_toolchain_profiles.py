"""RM-07 fail-closed host toolchains/matrix and opt-in compiler cache."""
from __future__ import annotations
import os
from pathlib import Path
import pytest

from devtool.actions_graph_export import compile_graph, workflow_yaml, run_node
from devtool.actions_language_providers import provider_export, provider_doctor, ProviderError
from devtool.toolchain_profiles import (select_profiles, cache_policy, ProfileError, PROFILES,
                                        capability_report, inspect_local_tools, MAX_TOTAL_JOBS)
from tests.test_next03_language_providers import fixture, PIN, git


def test_profiles_explicit_hosted_and_local_rejection():
    assert [x.identifier for x in select_profiles('')] == ['ubuntu-24.04-x64']
    assert [x.identifier for x in select_profiles('ubuntu-24.04-x64,macos-15-intel')] == ['ubuntu-24.04-x64','macos-15-intel']
    assert PROFILES['termux-android-arm64'].hosted_runner is None
    assert {'dotnet','cpp'}.issubset(set(select_profiles('windows-2022-x64')[0].supported_providers))
    for bad in ['unknown','termux-android-arm64',
                'ubuntu-24.04-x64,ubuntu-24.04-x64',
                'ubuntu-24.04-x64,macos-15-intel,windows-2022-x64',
                'ubuntu-24.04-x64,$(echo hi)','', 'a'*161]:
        if bad=='': continue
        with pytest.raises(ProfileError):select_profiles(bad)


def test_matrix_isolation_exact_needs_handoffs_and_determinism(tmp_path):
    root=fixture(tmp_path)
    args={'target':'app','workflow':'ci','devtool_sha':PIN,
          'host_profiles':'ubuntu-24.04-x64,macos-15-intel'}
    graph=compile_graph(root,**args)
    assert len(graph['jobs'])==4
    assert [job['runner'] for job in graph['jobs']] == ['ubuntu-24.04']*2+['macos-15-intel']*2
    assert graph['jobs'][1]['needs']==[graph['jobs'][0]['id']]
    assert graph['jobs'][3]['needs']==[graph['jobs'][2]['id']]
    assert set(graph['jobs'][3]['needs']).isdisjoint(set(graph['jobs'][:2][0]['needs']))
    assert graph['cache']=='disabled'
    rendered=workflow_yaml(graph)
    assert rendered.count('runs-on: macos-15-intel')==2
    assert rendered.count('runs-on: ubuntu-24.04')==2
    assert '--host-profiles ubuntu-24.04-x64,macos-15-intel' in rendered
    assert rendered==workflow_yaml(compile_graph(root,**args))
    assert provider_export(root,mode='multi-job',**args)['graph_sha256']==graph['graph_sha256']
    assert compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)['graph_sha256']!=graph['graph_sha256']


def test_hosted_profile_refuses_single_job_and_windows(tmp_path):
    root=fixture(tmp_path)
    with pytest.raises(ProviderError,match='multi-job'):
        provider_export(root,target='app',workflow='ci',devtool_sha=PIN,host_profiles='macos-15-intel')
    with pytest.raises(ProviderError,match='windows-2022-x64'):
        compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,host_profiles='windows-2022-x64')


def test_cache_opt_in_go_pinned_and_untrusted_lock_failclosed(tmp_path):
    root=fixture(tmp_path)
    p=root/'go.sum'
    profile=PROFILES['ubuntu-24.04-x64']
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    row=graph['provider_plan']['nodes'][0]['provider']
    assert cache_policy(root,row,profile,enabled=False)['enabled'] is False
    with pytest.raises(ProviderError,match='go.sum'):
        compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,cache_locks=True)
    p.write_text('example.com/test v1.0.0 h1:abcdef\n')
    git(root,'add','go.sum');git(root,'-c','user.name=Fixture','-c','user.email=fixture@example.org','commit','-qm','go lock')
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,cache_locks=True)
    assert graph['jobs'][0]['cache_policy']['enabled']
    assert not graph['jobs'][1]['cache_policy']['enabled']
    assert 'actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830' in workflow_yaml(graph)
    assert 'path: ~/.cache/go-build' in workflow_yaml(graph)
    assert 'cache: false' in workflow_yaml(graph)
    assert 'restore-keys:' not in workflow_yaml(graph)
    assert not any(x in workflow_yaml(graph) for x in ('~/.ssh','~/.aws','private-token'))
    # Source change changes cached key even before commit.
    first=graph['jobs'][0]['cache_policy']['key']
    p.write_text('example.com/test v1.0.0 h1:NEW\n')
    after=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,cache_locks=True)
    assert first != after['jobs'][0]['cache_policy']['key']
    p.unlink();p.symlink_to(root/'go.mod')
    with pytest.raises(ProviderError,match='go.sum|symlinked'):
        compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,cache_locks=True)


def test_doctor_unknown_separate_from_missing_and_no_install(tmp_path,monkeypatch):
    root=fixture(tmp_path)
    import devtool.toolchain_profiles as mod
    monkeypatch.setattr(mod,'inspect_local_tools',lambda:{'go':{'state':'unknown','version':None},
                                                       'python':{'state':'missing','version':None}})
    report=provider_doctor(root,all_profiles=True)
    data=report['toolchains']
    assert data['installed']['go']['state']=='unknown' and data['installed']['python']['state']=='missing'
    assert len(data['profiles'])==4
    assert data['no_automatic_installation'] and not data['remote_attested']
    assert report['all_profiles_requested']


def test_runner_refuses_mismatched_hosted_profile_before_execution(tmp_path,monkeypatch):
    root=fixture(tmp_path)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,host_profiles='macos-15-intel')
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','54')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    monkeypatch.setenv('RUNNER_OS','Linux')
    monkeypatch.setenv('RUNNER_ARCH','X64')
    with pytest.raises(ProviderError,match='runner OS'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                 job_id=graph['jobs'][0]['id'],inputs=tmp_path/'input',outputs=tmp_path/'output',
                 host_profiles='macos-15-intel')


def test_cli_schema_exposes_new_review_controls():
    from devtool.cli import build_parser
    p=build_parser()
    parsed=p.parse_args(['actions','provider-export','--target','app','--workflow','ci',
                         '--devtool-sha',PIN,'--mode','multi-job',
                         '--host-profiles','ubuntu-24.04-x64,macos-15-intel','--cache-locks','--plan'])
    assert parsed.provider_cache_locks and parsed.provider_review_plan
    assert parsed.provider_host_profiles.endswith('macos-15-intel')
    doctor=p.parse_args(['actions','provider-doctor','--all','--json'])
    assert doctor.provider_all_profiles and doctor.json


def test_macos_cache_path_is_system_specific(tmp_path):
    root=fixture(tmp_path)
    (root/'go.sum').write_text('example.com/test v1.0.0 h1:test\n')
    git(root,'add','go.sum');git(root,'-c','user.name=Fixture','-c','user.email=fixture@example.org','commit','-qm','go lock')
    plan=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,
                       host_profiles='ubuntu-24.04-x64,macos-15-intel',cache_locks=True)
    assert plan['jobs'][0]['cache_policy']['path']=='~/.cache/go-build'
    assert plan['jobs'][2]['cache_policy']['path']=='~/Library/Caches/go-build'
    assert plan['jobs'][0]['cache_policy']['key']!=plan['jobs'][2]['cache_policy']['key']
    assert 'path: ~/Library/Caches/go-build' in workflow_yaml(plan)


def test_doctor_describes_requested_pins_and_next_actions(tmp_path,monkeypatch):
    root=fixture(tmp_path)
    import devtool.toolchain_profiles as mod
    monkeypatch.setattr(mod,'inspect_local_tools',lambda:{})
    report=provider_doctor(root)
    ok=report['workflows'][0]
    assert ok['state']=='locally_eligible' and ok['next_action']
    assert {row['toolchain'] for row in ok['requested_toolchains']}=={'go','python'}
    assert {row['expected_hosted_runner'] for row in ok['requested_toolchains']}=={'ubuntu-24.04'}


def test_macos_host_requirements_refuse_cross_os_cell(tmp_path):
    root=fixture(tmp_path)
    cfg=root/'.devtool.toml'
    cfg.write_text(cfg.read_text().replace('inputs = ["go.mod"]',
                                          'inputs = ["go.mod"]\n[targets.app.jobs.go_tests.metadata.ci_host_requirements]\nos = "linux"'))
    assert compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)['jobs']
    with pytest.raises(ProviderError,match='host profile macos'):
        compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,
                      host_profiles='ubuntu-24.04-x64,macos-15-intel')
