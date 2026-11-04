"""DN03-R5: real monorepo selected-project closure without unsound project admission."""
from pathlib import Path
import json
import subprocess
import pytest
from devtool.actions_rm09_dotnet import solution_graph, inspect_dotnet_job, DotNetError


def fixture(tmp_path):
    root=tmp_path/'mono'
    root.mkdir()
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'config','user.name','DN03 Tests'],check=True)
    subprocess.run(['git','-C',str(root),'config','user.email','tests@example.invalid'],check=True)
    def put(name,content):
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content,encoding='utf-8')
    put('global.json',json.dumps({'sdk':{'version':'10.0.100','rollForward':'latestFeature','allowPrerelease':False}}))
    put('Directory.Build.props','<Project><PropertyGroup Condition="true"><GamePath>dynamic</GamePath></PropertyGroup></Project>')
    put('Unrelated/Game.csproj','<Project><PropertyGroup><TargetFramework>net6.0-windows</TargetFramework></PropertyGroup></Project>')
    put('Lib/Directory.Build.props','<Project><PropertyGroup><TargetFramework>net6.0</TargetFramework></PropertyGroup></Project>')
    put('Tests/Directory.Build.props','<Project><PropertyGroup><TargetFramework>net6.0</TargetFramework></PropertyGroup></Project>')
    put('Lib/Core.csproj','<Project><PropertyGroup><TargetFramework>net6.0</TargetFramework></PropertyGroup></Project>')
    put('Tests/Unit.csproj','<Project><PropertyGroup><TargetFramework>net6.0</TargetFramework></PropertyGroup><ItemGroup><ProjectReference Include="../Lib/Core.csproj" /></ItemGroup></Project>')
    for name in ('Lib','Tests'):
        put(name+'/packages.lock.json',json.dumps({'version':1,'dependencies':{'net6.0':{}}}))
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'commit','-qm','fixture'],check=True)
    return root


def test_scoped_monorepo_keeps_real_reference_closure(tmp_path):
    root=fixture(tmp_path)
    with pytest.raises(DotNetError,match='Directory.Build.props'):
        solution_graph(root,selected='Tests/Unit.csproj')
    review=solution_graph(root,selected='Tests/Unit.csproj',scoped=True)
    assert review['selected_projects']==['Lib/Core.csproj','Tests/Unit.csproj']
    assert review['selected_lockfiles']==['Lib/packages.lock.json','Tests/packages.lock.json']
    assert 'Unrelated/Game.csproj' not in review['sources']
    assert 'global.json' in review['sources']
    assert 'Lib/Directory.Build.props' in review['sources']
    assert 'Tests/Directory.Build.props' in review['sources']
    args=['dotnet','test','Tests/Unit.csproj','--no-restore','--logger','trx;LogFileName=results.trx','--results-directory','dist/unit']
    p=inspect_dotnet_job(root,args,{'ci_dotnet_allow_eol':True,'ci_dotnet_selected_project_only':True})
    assert p['selected_project_only'] is True
    assert p['selected']=='Tests/Unit.csproj'
    with pytest.raises(DotNetError,match='scope opt-in'):
        inspect_dotnet_job(root,args,{'ci_dotnet_selected_project_only':'true'})


def test_scope_fails_closed_on_broken_or_untracked_reference(tmp_path):
    root=fixture(tmp_path)
    proj=root/'Tests/Unit.csproj'
    proj.write_text(proj.read_text().replace('../Lib/Core.csproj','../Untracked/Hidden.csproj'))
    with pytest.raises(DotNetError,match='scoped ProjectReference'):
        solution_graph(root,selected='Tests/Unit.csproj',scoped=True)
    with pytest.raises(DotNetError,match='tracked .csproj'):
        solution_graph(root,selected='Unrelated/Missing.csproj',scoped=True)
    with pytest.raises(DotNetError,match='boolean'):
        solution_graph(root,selected='Tests/Unit.csproj',scoped='yes')
