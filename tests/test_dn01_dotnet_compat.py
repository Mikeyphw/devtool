"""DN01 regression coverage: .NET 6, static props, scoped locks, path safety."""

import pytest

from devtool.actions_rm09_dotnet import DotNetError, inspect_dotnet_job, solution_graph
from tests.test_rm09_dotnet import dotnet_repo, git


def static_net6_repo(tmp_path):
    root = dotnet_repo(tmp_path)
    for name in ('Lib', 'App'):
        project = root / name / (name + '.csproj')
        project.write_text(project.read_text().replace(
            '<TargetFramework>net8.0</TargetFramework>', ''), encoding='utf-8')
        (root / name / 'packages.lock.json').write_text(
            '{"version":1,"dependencies":{"net6.0":{}}}', encoding='utf-8')
    (root / 'Directory.Build.props').write_text(
        '<Project><PropertyGroup><TargetFramework>net6.0</TargetFramework>'
        '</PropertyGroup></Project>', encoding='utf-8')
    (root / 'global.json').write_text(
        '{"sdk":{"version":"10.0.100","rollForward":"disable"}}', encoding='utf-8')
    git(root, 'add', '.')
    return root


def test_dn01_static_inheritance_separates_sdk_runtime_and_eol_status(tmp_path):
    root = static_net6_repo(tmp_path)
    graph = solution_graph(root, selected='App/App.csproj')
    assert graph['sdk_version'] == '10.0.100'
    assert graph['projects']['App/App.csproj']['tfms'] == ['net6.0']
    assert graph['legacy_tfms'] == ['net6.0']
    assert graph['selected_topological_projects'] == ['Lib/Lib.csproj', 'App/App.csproj']
    assert graph['selected_lockfiles'] == [
        'App/packages.lock.json', 'Lib/packages.lock.json']
    assert graph['sources']['Directory.Build.props']

    argv = ['dotnet', 'build', 'App/App.csproj', '--no-restore']
    with pytest.raises(DotNetError, match='end-of-support'):
        inspect_dotnet_job(root, argv, {})
    review = inspect_dotnet_job(root, argv, {'ci_dotnet_allow_eol': True})
    assert review['version'] == '10.0.100'
    assert review['required_runtime_families'] == ['6.0']
    assert review['legacy_tfms'] == ['net6.0']
    assert review['eol_opt_in'] is True
    assert review['release_qualified'] is False
    with pytest.raises(DotNetError, match='must be boolean'):
        inspect_dotnet_job(root, argv, {'ci_dotnet_allow_eol': 'true'})


def test_dn01_selected_project_closure_only_requires_relevant_locks(tmp_path):
    root = dotnet_repo(tmp_path)
    git(root, 'rm', 'App/packages.lock.json')
    graph = solution_graph(root, selected='Lib/Lib.csproj')
    assert graph['selected_projects'] == ['Lib/Lib.csproj']
    assert graph['selected_lockfiles'] == ['Lib/packages.lock.json']
    assert 'App/packages.lock.json' not in graph['sources']
    with pytest.raises(DotNetError, match='missing selected project NuGet lock'):
        solution_graph(root, selected='All.sln')
    with pytest.raises(DotNetError, match='missing selected project NuGet lock'):
        solution_graph(root, selected='App/App.csproj')


def test_dn01_unrelated_malformed_lock_is_not_loaded(tmp_path):
    root = dotnet_repo(tmp_path)
    (root / 'App/packages.lock.json').write_text('broken JSON', encoding='utf-8')
    assert solution_graph(root, selected='Lib/Lib.csproj')['selected_lockfiles'] == [
        'Lib/packages.lock.json']
    with pytest.raises(DotNetError, match='malformed NuGet lock'):
        solution_graph(root, selected='All.sln')


def test_dn01_windows_relative_reference_and_rejections(tmp_path):
    root = dotnet_repo(tmp_path)
    project = root / 'App/App.csproj'
    original = project.read_text()
    project.write_text(original.replace('../Lib/Lib.csproj', r'..\Lib\Lib.csproj'))
    graph = solution_graph(root, selected='App/App.csproj')
    assert graph['edges']['App/App.csproj'] == ['Lib/Lib.csproj']

    for bad in (r'..\..\..\outside.csproj', r'C:\outside.csproj',
                r'\\server\share.csproj', r'$(Dir)\Lib.csproj',
                '/absolute/Lib.csproj'):
        project.write_text(original.replace('../Lib/Lib.csproj', bad))
        with pytest.raises(DotNetError):
            solution_graph(root, selected='App/App.csproj')


def test_dn01_props_untracked_and_dynamic_fail_closed(tmp_path):
    root = dotnet_repo(tmp_path)
    props = root / 'Directory.Build.props'
    props.write_text(
        '<Project><PropertyGroup><TargetFramework>net6.0</TargetFramework>'
        '</PropertyGroup></Project>')
    with pytest.raises(DotNetError, match='tracked Directory.Build.props'):
        solution_graph(root, selected='All.sln')

    git(root, 'add', 'Directory.Build.props')
    for xml in (
        '<Project><Import Project="other.props" /></Project>',
        '<Project><PropertyGroup Condition="true">'
        '<TargetFramework>net6.0</TargetFramework></PropertyGroup></Project>',
        '<Project><PropertyGroup><TargetFramework>$(TargetVersion)</TargetFramework>'
        '</PropertyGroup></Project>',
    ):
        props.write_text(xml)
        with pytest.raises(DotNetError, match='Directory.Build.props'):
            solution_graph(root, selected='All.sln')


def test_dn01_nearest_props_and_inline_project_override(tmp_path):
    root = dotnet_repo(tmp_path)
    (root / 'Directory.Build.props').write_text(
        '<Project><PropertyGroup><TargetFramework>net6.0</TargetFramework>'
        '</PropertyGroup></Project>')
    (root / 'Lib/Directory.Build.props').write_text(
        '<Project><PropertyGroup><TargetFramework>net9.0</TargetFramework>'
        '</PropertyGroup></Project>')
    lib = root / 'Lib/Lib.csproj'
    lib.write_text(lib.read_text().replace(
        '<TargetFramework>net8.0</TargetFramework>', ''))
    git(root, 'add', '.')
    graph = solution_graph(root, selected='All.sln')
    assert graph['projects']['Lib/Lib.csproj']['tfms'] == ['net9.0']
    assert graph['projects']['App/App.csproj']['tfms'] == ['net8.0']
    assert graph['legacy_tfms'] == []
    assert 'Lib/Directory.Build.props' in graph['sources']


def test_dn01_unreviewed_framework_still_rejected(tmp_path):
    root = dotnet_repo(tmp_path)
    project = root / 'App/App.csproj'
    project.write_text(project.read_text().replace('net8.0', 'net7.0'))
    with pytest.raises(DotNetError, match='reviewed'):
        solution_graph(root, selected='All.sln')
