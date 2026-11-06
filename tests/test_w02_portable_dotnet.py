"""W02: native Linux/Windows .NET provider plan is evaluated per host.

No local dotnet/Gradle, network, runtime install or signing is performed.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from devtool.actions_graph_export import compile_graph, workflow_yaml
from devtool.actions_language_providers import ProviderError
from tests.test_rm09_dotnet import dotnet_repo
from tests.test_next03_language_providers import PIN, fixture


HOSTS='ubuntu-24.04-x64,windows-2022-x64'


def test_two_host_native_dotnet_graph_is_reviewed_and_isolated(tmp_path):
    root=dotnet_repo(tmp_path)
    kw={'target':'app','workflow':'ci','devtool_sha':PIN,'host_profiles':HOSTS}
    graph=compile_graph(root,**kw)
    assert len(graph['jobs'])==4
    assert list(graph['host_provider_plan_sha256'])==HOSTS.split(',')
    linux,windows=graph['jobs'][:2],graph['jobs'][2:]
    assert [j['profile_id'] for j in linux]==['ubuntu-24.04-x64']*2
    assert [j['profile_id'] for j in windows]==['windows-2022-x64']*2
    assert [j['runner'] for j in linux]==['ubuntu-24.04']*2
    assert [j['runner'] for j in windows]==['windows-2022']*2
    for pair in (linux, windows):
        assert pair[0]['needs']==[]
        assert pair[1]['needs']==[pair[0]['id']]
        assert all(j['provider']=='dotnet' for j in pair)
    assert graph['graph_sha256']==compile_graph(root,**kw)['graph_sha256']
    yaml=workflow_yaml(graph)
    assert 'runs-on: windows-2022' in yaml and 'runs-on: ubuntu-24.04' in yaml
    assert 'actions/setup-dotnet@' in yaml
    assert graph['release_qualified'] is False


def test_host_specific_dotnet_plan_drift_is_refused(tmp_path):
    root=dotnet_repo(tmp_path)
    from devtool import actions_graph_export as exporter
    original=exporter.provider_plan

    def tampered(*args, **kwargs):
        p=deepcopy(original(*args, **kwargs))
        if kwargs.get('host_os')=='windows':
            p['nodes'][0]['provider']['canonical_argv'].append('--invented')
        return p

    with patch.object(exporter,'provider_plan',side_effect=tampered):
        with pytest.raises(ProviderError,match='W02 .* drifted'):
            compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,host_profiles=HOSTS)


def test_windows_only_project_is_not_smuggled_into_linux_cell(tmp_path):
    root=dotnet_repo(tmp_path,windows=True)
    with pytest.raises(ProviderError):
        compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,host_profiles=HOSTS)


def test_non_dotnet_mixed_windows_still_refused(tmp_path):
    root=fixture(tmp_path)
    with pytest.raises(ProviderError,match='mixed Windows/POSIX'):
        compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,host_profiles=HOSTS)
