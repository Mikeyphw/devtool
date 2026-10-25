"""FC-03 roadmap counterexamples: conservative source selection and static admission."""
import subprocess
from pathlib import Path
import pytest

from devtool.onboarding import inventory
from devtool.actions_rm11_node import affected_packages,node_workspace
from devtool.actions_rm12_jvm import jvm_project,JvmError
from tests.test_rm11_node import fixture as node_fixture
from tests.test_rm20_onboarding import repo as build_repo


def _commit(root):
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Fixture','-c','user.email=t@invalid','commit','-qm','updated'],check=True)


def test_root_source_and_shared_tsconfig_select_all(tmp_path):
    root=node_fixture(tmp_path)
    ws=node_workspace(root)
    expected=set(ws['packages'])
    for changed in ('src/index.ts','tsconfig.json','config/eslint.base.js',
                    'src/shared/index.ts','shared/logger.ts','custom.lock'):
        assert set(affected_packages(ws,[changed]))==expected, changed
    assert affected_packages(ws,[])==[]
    assert set(affected_packages(ws,['packages/core/src/index.ts']))=={
        'packages/core','packages/web'}


def _gradle_repo(tmp_path,plugin, *, alias_id=None):
    files={'settings.gradle.kts':'rootProject.name = "demo"\n',
           'build.gradle.kts':plugin+'\n',
           'gradle.lockfile':'dummy=one\n'}
    if alias_id is not None:
        files['gradle/libs.versions.toml']='[plugins]\nandroid-application = { id = "'+alias_id+'", version = "8.7.1" }\n'
    return build_repo(tmp_path,files)


def test_java_only_gradle_classified_jvm_not_android(tmp_path):
    root=_gradle_repo(tmp_path,'plugins { java }')
    plan=inventory(root)
    assert plan['status']=='REVIEW_REQUIRED' and not plan['safe_to_apply']
    assert plan['targets'][0]['runner']=='gradle'
    assert plan['targets'][0]['platform']=='jvm'
    assert jvm_project(root,manager='gradle')['manager']=='gradle'


def test_alias_android_plugin_routes_away_from_jvm(tmp_path):
    root=_gradle_repo(tmp_path,'plugins { alias(libs.plugins.android.application) }',alias_id='com.android.application')
    plan=inventory(root)
    assert plan['status']=='REVIEW_REQUIRED' and not plan['safe_to_apply']
    assert plan['targets'][0]['runner']=='android'
    with pytest.raises(JvmError,match='Android'):
        jvm_project(root,manager='gradle')


def test_alias_jvm_plugin_is_not_android(tmp_path):
    root=_gradle_repo(tmp_path,'plugins { alias(libs.plugins.android.application) }',alias_id='java-library')
    plan=inventory(root)
    assert plan['targets'][0]['runner']=='gradle'
    assert jvm_project(root,manager='gradle')['manager']=='gradle'


@pytest.mark.parametrize('plugin,catalog',[
    ('plugins { alias(libs.plugins.android.application) }',None),
    ('plugins { alias(libs.plugins.android.application) }','invalid'),
    ('plugins { alias(libs.plugins.unknown) }','valid'),
    ('plugins { id(someVariable) }',None),
    ('plugins { id("unknown.vendor.plugin") }',None),
])
def test_dynamic_unknown_plugins_never_auto_admitted(tmp_path,plugin,catalog):
    root=_gradle_repo(tmp_path,plugin,alias_id=('java' if catalog=='valid' else None))
    plan=inventory(root)
    assert plan['status']=='REVIEW_REQUIRED'
    assert not plan['safe_to_apply']
    with pytest.raises(JvmError,match='Gradle|plugin'):
        jvm_project(root,manager='gradle')


@pytest.mark.parametrize('files,runner',[
    ({'app/app.csproj':'<Project />'},'dotnet'),
    ({'app/app.slnx':'<Solution />'},'dotnet'),
    ({'lib/CMakeLists.txt':'cmake_minimum_required(VERSION 3.25)'},'cmake'),
    ({'lib/meson.build':"project('demo','c')"},'meson'),
    ({'lib/pom.xml':'<project></project>'},'maven'),
])
def test_more_native_project_roots_present(tmp_path,files,runner):
    root=build_repo(tmp_path,files)
    plan=inventory(root)
    assert plan['status']=='REVIEW_REQUIRED' and not plan['safe_to_apply']
    assert plan['targets'][0]['runner']==runner


def test_polyglot_roots_cannot_omit_project(tmp_path):
    root=build_repo(tmp_path,{
        'a/go.mod':'module example.org/a\n',
        'b/core.csproj':'<Project />',
        'c/CMakeLists.txt':'project(c)',
        'README.md':'hi'})
    report=inventory(root)
    assert report['status']=='REVIEW_REQUIRED' and not report['safe_to_apply']
    assert set(x['runner'] for x in report['targets'])=={'go','dotnet','cmake'}
    assert report['inventory']['unowned_paths']==['README.md']


def test_mixed_known_unknown_root_requires_review(tmp_path):
    root=build_repo(tmp_path,{'go.mod':'module example.org/demo\n',
                              'engine/build.sbt':'name := "engine"\n'})
    plan=inventory(root)
    assert plan['status']=='REVIEW_REQUIRED' and not plan['safe_to_apply']
    assert any('unrecognized' in reason for reason in plan['review_issues'])


def test_gradle_root_nested_build_requires_review(tmp_path):
    root=build_repo(tmp_path,{'build.gradle.kts':'plugins { java }',
                              'app/build.gradle.kts':'plugins { java }',
                              'settings.gradle.kts':'rootProject.name = "example"'})
    plan=inventory(root)
    assert plan['status']=='REVIEW_REQUIRED'
    assert any('overlap' in reason for reason in plan['review_issues'])


def test_shell_only_detected_but_requires_review(tmp_path):
    root=build_repo(tmp_path,{'scripts/check.sh':'#!/bin/sh\ntrue\n'})
    plan=inventory(root)
    assert plan['status']=='REVIEW_REQUIRED'
    assert any(t['runner']=='command' for t in plan['targets'])


def test_detected_source_is_read_only_and_never_runs_scripts(tmp_path):
    root=build_repo(tmp_path,{'build.gradle.kts':'plugins { id("com.android.application") }',
                              'scripts/pwn.sh':'echo bad > touched'})
    report=inventory(root)
    assert report['targets'][0]['runner']=='android'
    assert not (root/'touched').exists()
    assert subprocess.run(['git','-C',str(root),'status','--porcelain'],capture_output=True,text=True,check=True).stdout==''
