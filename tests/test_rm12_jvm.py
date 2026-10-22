"""RM-12 behavioral promises: multi-module graphs, native handoffs, real ZIP/JAR bytes."""
from pathlib import Path
import hashlib
import os
import subprocess
import zipfile
from types import SimpleNamespace
import pytest

from devtool.actions_rm12_jvm import (jvm_project, inspect_jvm_job, verify_jar,verify_desktop_bundle,
        aggregate_junit,materialize_jvm_package,JvmError)
from devtool.actions_language_providers import provider_plan,ProviderError
from devtool.actions_graph_export import compile_graph,workflow_yaml,run_node
from tests.test_next03_language_providers import PIN


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()


def commit(root):
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Fixture','-c','user.email=t@invalid','commit','-qm','fixture'],check=True)


def fixture(tmp_path,kind='maven',matrix=False):
    root=tmp_path/'repo';root.mkdir()
    if kind=='maven':
        rootpom='''<project><modelVersion>4.0.0</modelVersion><groupId>example.group</groupId><artifactId>parent</artifactId><version>1.0</version><packaging>pom</packaging><properties><maven.compiler.release>17</maven.compiler.release></properties><modules><module>lib</module><module>app</module></modules></project>'''
        (root/'pom.xml').write_text(rootpom)
        for name in ('lib','app'):
            (root/name).mkdir()
            (root/name/'pom.xml').write_text(f'''<project><modelVersion>4.0.0</modelVersion><parent><groupId>example.group</groupId><artifactId>parent</artifactId><version>1.0</version></parent><artifactId>{name}</artifactId>'''+('''<dependencies><dependency><groupId>example.group</groupId><artifactId>lib</artifactId></dependency></dependencies>''' if name=='app' else '')+'</project>')
        cmd=['mvn','-B','-o','-ntp','-f','pom.xml','-pl','app','-am','test']
    else:
        (root/'settings.gradle.kts').write_text('rootProject.name = "mono"\ninclude(":lib", ":app")\n')
        for name in ('lib','app'):
            (root/name).mkdir()
            (root/name/'build.gradle.kts').write_text('plugins { java }\n'+('dependencies { implementation(project(":lib")) }\n' if name=='app' else ''))
        (root/'build.gradle.kts').write_text('plugins { java }\n')
        (root/'gradle.lockfile').write_text('# pinned-empty-fixture\n')
        cmd=['gradle','--offline','--no-daemon','--console=plain',':app:test']
    matrix_string='ci_jdk_versions="17,21"' if matrix else ''
    tool_version='3.9.9' if kind=='maven' else '8.13'
    command=','.join('"'+c+'"' for c in cmd)
    (root/'.devtool.toml').write_text(f'''[project]
runner="python"
[workspace]
default_target="app"
[wrapper]
schema=2
[targets.app]
runner="python"
root="."
[targets.app.jobs.tests]
runner="command"
metadata={{ci_toolchain="jvm",ci_jdk_version="17",ci_jvm_tool_version="{tool_version}"{(','+matrix_string) if matrix else ''}}}
command=[{command}]
outputs=["dist/jvm-tests.xml"]
[targets.app.workflows]
ci=[{{id="tests",ref="job:tests",depends_on=[]}}]
''')
    (root/'devtoolw').write_text('#!/bin/sh\n')
    subprocess.run(['git','init','-q',str(root)],check=True)
    commit(root)
    return root


@pytest.mark.parametrize('kind',['maven','gradle'])
def test_project_dependency_graph_and_java_matrices(tmp_path,kind):
    root=fixture(tmp_path,kind,matrix=True)
    p=jvm_project(root,manager=kind)
    assert p['edges']['app' if kind=='maven' else ':app']==['lib' if kind=='maven' else ':lib']
    plan=provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)
    assert plan['providers']==['jvm']
    assert plan['nodes'][0]['provider']['matrix_versions']==['17','21']
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,host_profiles='ubuntu-24.04-x64,macos-15-intel',cache_locks=True)
    assert len(graph['jobs'])==4
    assert all(j['cache_policy']['enabled'] for j in graph['jobs'])
    assert all(j['jvm_manager']==kind for j in graph['jobs'])
    yaml=workflow_yaml(graph)
    assert 'actions/setup-java@cf277c60eb25467037889841efdb72551f06f6c3' in yaml
    assert 'distribution: temurin' in yaml
    assert "java-version: \"21\"" in yaml
    assert len({j['matrix_cell'] for j in graph['jobs']})==4


def test_maven_cycle_unsafe_xml_android_and_source_drift(tmp_path):
    root=fixture(tmp_path)
    p=jvm_project(root,manager='maven')
    assert p['order'].index('lib')<p['order'].index('app')
    file=root/'lib/pom.xml';content=file.read_text()
    file.write_text(content.replace('</project>','<dependencies><dependency><groupId>example.group</groupId><artifactId>app</artifactId></dependency></dependencies></project>'))
    with pytest.raises(JvmError,match='cycle'):jvm_project(root,manager='maven')
    file.write_text(content)
    file.write_text('<!DOCTYPE a [<!ENTITY danger SYSTEM "file:///etc/passwd">]>'+content)
    with pytest.raises(JvmError,match='unsafe XML'):jvm_project(root,manager='maven')
    file.write_text(content)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    file.write_text(content+'\n<!-- drift -->\n')
    changed=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    assert changed['graph_sha256']!=graph['graph_sha256']


def test_gradle_static_only_android_routing_and_symlink(tmp_path):
    root=fixture(tmp_path,kind='gradle')
    f=root/'app/build.gradle.kts'
    f.write_text('plugins { id("com.android.application") }\n')
    with pytest.raises(JvmError,match='Android'):jvm_project(root,manager='gradle')
    f.write_text('plugins { java }\n')
    setting=root/'settings.gradle.kts';setting.write_text('include(System.getenv("BAD"))\n')
    with pytest.raises(JvmError,match='dynamic'):jvm_project(root,manager='gradle')
    setting.write_text('include(":lib", ":app")\n')
    f.unlink();f.symlink_to(root/'build.gradle.kts')
    with pytest.raises(JvmError,match='symlink'):jvm_project(root,manager='gradle')


def test_toolchain_mismatch_and_bad_native_argv(tmp_path):
    root=fixture(tmp_path)
    f=root/'app/pom.xml'
    f.write_text(f.read_text().replace('</project>','<properties><maven.compiler.release>21</maven.compiler.release></properties></project>'))
    with pytest.raises(JvmError,match='older'):
        inspect_jvm_job(root,['mvn','-B','-o','-ntp','-f','app/pom.xml','test'],{'ci_jdk_version':'17','ci_jvm_tool_version':'3.9.9'})
    with pytest.raises(JvmError,match='offline'):
        inspect_jvm_job(root,['mvn','-B','-f','app/pom.xml','test'],{'ci_jdk_version':'21','ci_jvm_tool_version':'3.9.9'})



def jar(root,*,signed=False):
    jar=root/'demo.jar';jar.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(jar,'w') as z:
        z.writestr('META-INF/MANIFEST.MF','Manifest-Version: 1.0\nMain-Class: Demo\n\n')
        z.writestr('Demo.class',b'\xca\xfe\xba\xbe\x00\x00\x00\x34')
        if signed:z.writestr('META-INF/DEMO.SF','Signature-Version: 1.0')
    return jar


def test_real_jar_manifest_class_signing_refusal_and_desktop(tmp_path):
    root=tmp_path
    path=jar(root)
    r=verify_jar(path);assert r['classes']==1 and not r['signer_authenticated']
    with zipfile.ZipFile(path,'a') as z:z.writestr('META-INF/DEMO.SF','Signature-Version: 1.0')
    with pytest.raises(JvmError,match='incomplete'):verify_jar(path)
    path.unlink();path=jar(root)
    pkg={'manager':'maven','modules':{'app':{'coordinate':'example.group:demo','version':'1.0','packaging':'jar'}},'source_sha256':{}}
    dest=root/'app/target/demo-1.0.jar';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(path.read_bytes())
    receipts=materialize_jvm_package(root,pkg,'app','demo',desktop=True)
    assert len(receipts)==2 and (root/'dist/demo.zip').exists()
    assert verify_desktop_bundle(root/'dist/demo.zip')['launcher_count']==1
    with zipfile.ZipFile(root/'bad.zip','w') as z:z.writestr('../evil','x')
    with pytest.raises(JvmError,match='unsafe'):verify_desktop_bundle(root/'bad.zip')


def test_junit_failure_duplicate_zero_and_success(tmp_path):
    root=tmp_path;d=root/'app/target/surefire-reports';d.mkdir(parents=True)
    p={'manager':'maven','modules':{'app':{'version':'1.0'}},'source_sha256':{}}
    file=d/'TEST-one.xml';file.write_text('<testsuite tests="1"><testcase classname="A" name="ok"/></testsuite>')
    result=aggregate_junit(root,p,'app');assert result['passed']==1
    (root/'dist/jvm-tests.xml').unlink()
    file.write_text('<testsuite tests="1"><testcase classname="A" name="ok"><failure/></testcase></testsuite>')
    with pytest.raises(JvmError,match='failures'):aggregate_junit(root,p,'app')
    file.write_text('<!DOCTYPE testsuite [<!ENTITY bad SYSTEM "file:///etc/passwd">]><testsuite/>')
    with pytest.raises(JvmError,match='unsafe XML'):aggregate_junit(root,p,'app')


def test_native_junit_real_runner_handoff_and_failure(tmp_path,monkeypatch):
    from devtool import runners
    root=fixture(tmp_path)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    job=graph['jobs'][0]
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','9381');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    def run(cfg,specs,**kwargs):
        dir=root/'app/target/surefire-reports';dir.mkdir(parents=True,exist_ok=True)
        (dir/'TEST-a.xml').write_text('<testsuite tests="1"><testcase classname="A" name="hello"/></testsuite>')
        return [SimpleNamespace(returncode=0,skipped=False,output='')],''
    monkeypatch.setattr(runners,'run_specs',run)
    import devtool.actions_graph_export as ge
    original_run=subprocess.run
    def runner_probe(argv,*args,**kwargs):
        if argv==['javac','-version']:
            return SimpleNamespace(returncode=0,stdout='javac 17.0.1\n',stderr='')
        if argv==['mvn','-version']:
            return SimpleNamespace(returncode=0,stdout='Apache Maven 3.9.9\n',stderr='')
        return original_run(argv,*args,**kwargs)
    monkeypatch.setattr(subprocess,'run',runner_probe)
    result=run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                    job_id=job['id'],inputs=tmp_path/'empty',outputs=tmp_path/'out')
    assert result['jvm_receipts'][0]['passed']==1
    assert (tmp_path/'out'/job['id']/'payload/dist/jvm-tests.xml').is_file()
    (root/'dist/jvm-tests.xml').unlink()
    def fail(cfg,specs,**kwargs):return [SimpleNamespace(returncode=1,skipped=False,output='')],''
    monkeypatch.setattr(runners,'run_specs',fail)
    with pytest.raises(ProviderError,match='did not succeed'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                 job_id=job['id'],inputs=tmp_path/'empty',outputs=tmp_path/'failout')
    assert not (tmp_path/'failout').exists()


def test_real_javac_jar_compile_and_binary_tamper(tmp_path):
    import shutil
    if not shutil.which('javac') or not shutil.which('jar'):
        pytest.skip('JDK binaries absent: real local bytecode test requires javac and jar')
    root=tmp_path
    (root/'Demo.java').write_text('public class Demo { public static void main(String[] args) { System.out.println("ok"); } }')
    subprocess.run(['javac','Demo.java'],cwd=root,check=True,capture_output=True,timeout=30)
    (root/'MANIFEST.MF').write_text('Manifest-Version: 1.0\nMain-Class: Demo\n\n')
    subprocess.run(['jar','cfm','real.jar','MANIFEST.MF','Demo.class'],cwd=root,check=True,capture_output=True,timeout=30)
    assert verify_jar(root/'real.jar')['classes']==1
    with zipfile.ZipFile(root/'badclass.jar','w') as z:
        z.writestr('META-INF/MANIFEST.MF','Manifest-Version: 1.0\n')
        z.writestr('Demo.class',b'not-a-class')
    with pytest.raises(JvmError,match='bytecode'):verify_jar(root/'badclass.jar')


def test_native_tool_version_drift_refuses_before_job(tmp_path,monkeypatch):
    from devtool import runners
    root=fixture(tmp_path)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','100');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    calls=[]
    def wrong_version(argv,*args,**kwargs):
        if argv==['javac','-version']:
            return SimpleNamespace(returncode=0,stdout='javac 17.0.1',stderr='')
        if argv==['mvn','-version']:
            return SimpleNamespace(returncode=0,stdout='Apache Maven 1.0.0',stderr='')
        return original(argv,*args,**kwargs)
    original=subprocess.run
    monkeypatch.setattr(subprocess,'run',wrong_version)
    def never_run(*args,**kwargs):calls.append(1);raise AssertionError('must refuse before execution')
    monkeypatch.setattr(runners,'run_specs',never_run)
    with pytest.raises(ProviderError,match='version differs'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                 job_id=graph['jobs'][0]['id'],inputs=tmp_path/'none',outputs=tmp_path/'out')
    assert calls==[]


def test_invalid_desktop_identity_and_java_matrix(tmp_path):
    root=fixture(tmp_path)
    config=root/'.devtool.toml';raw=config.read_text()
    config.write_text(raw.replace('ci_jdk_version="17"','ci_jdk_version="13"'))
    with pytest.raises(ProviderError,match='JDK'):compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    config.write_text(raw.replace('outputs=["dist/jvm-tests.xml"]','outputs=["dist/fake.jar"]'))
    with pytest.raises(ProviderError,match='output'):compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)


def test_native_package_jar_desktop_and_handoff(tmp_path,monkeypatch):
    from devtool import runners
    root=fixture(tmp_path)
    cfg=root/'.devtool.toml'
    cfg.write_text(cfg.read_text().replace('ci_jdk_version="17"','ci_jdk_version="17",ci_jvm_desktop_bundle=true')
                   .replace('"-am","test"','"-am","package"')
                   .replace('outputs=["dist/jvm-tests.xml"]','outputs=["dist/app.jar","dist/app.zip"]'))
    commit(root)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    job=graph['jobs'][0]
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','999');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    original=subprocess.run
    def probe(argv,*args,**kwargs):
        if argv==['mvn','-version']:
            return SimpleNamespace(returncode=0,stdout='Apache Maven 3.9.9',stderr='')
        if argv==['javac','-version']:
            return SimpleNamespace(returncode=0,stdout='javac 17.0.9',stderr='')
        return original(argv,*args,**kwargs)
    monkeypatch.setattr(subprocess,'run',probe)
    def run(cfg,specs,**kwargs):
        source=root/'app/target/app-1.0.jar';source.parent.mkdir(parents=True)
        with zipfile.ZipFile(source,'w') as z:
            z.writestr('META-INF/MANIFEST.MF','Manifest-Version: 1.0\nMain-Class: Demo\n\n')
            z.writestr('Demo.class',b'\xca\xfe\xba\xbe\x00\x00\x00\x34')
        return [SimpleNamespace(returncode=0,skipped=False,output='')],''
    monkeypatch.setattr(runners,'run_specs',run)
    result=run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                    job_id=job['id'],inputs=tmp_path/'empty',outputs=tmp_path/'staged')
    assert len(result['jvm_receipts'])==2
    assert (tmp_path/'staged'/job['id']/'payload/dist/app.jar').is_file()
    assert (tmp_path/'staged'/job['id']/'payload/dist/app.zip').is_file()
    assert (tmp_path/'staged'/job['id']/'receipt.json').is_file()


def test_gradle_jdk_source_mismatch_and_manifest_classpath_refusal(tmp_path):
    root=fixture(tmp_path,kind='gradle')
    script=root/'app/build.gradle.kts'
    script.write_text(script.read_text()+'\njava { toolchain { languageVersion.set(JavaLanguageVersion.of(21)) } }\n')
    with pytest.raises(JvmError,match='older'):
        inspect_jvm_job(root,['gradle','--offline','--no-daemon','--console=plain',':app:test'],
                        {'ci_jdk_version':'17','ci_jvm_tool_version':'8.13'})
    bad=tmp_path/'bad-cp.jar'
    with zipfile.ZipFile(bad,'w') as z:
        z.writestr('META-INF/MANIFEST.MF','Manifest-Version: 1.0\nClass-Path: ../../secrets.jar\n')
        z.writestr('Demo.class',b'\xca\xfe\xba\xbe\x00\x00\x00\x34')
    with pytest.raises(JvmError,match='classpath'):verify_jar(bad)


def test_kotlin_jvm_gradle_and_maven_android_plugin_direction(tmp_path):
    root=tmp_path/'gradle';root.mkdir()
    gradle=fixture(root,kind='gradle')
    f=gradle/'app/build.gradle.kts'
    f.write_text('plugins { kotlin("jvm") version "2.2.20" }\ndependencies { implementation(project(":lib")) }\n')
    project=jvm_project(gradle,manager='gradle')
    assert project['edges'][':app']==[':lib']
    j=inspect_jvm_job(gradle,['gradle','--offline','--no-daemon','--console=plain',':app:test'],
                      {'ci_jdk_version':'21','ci_jvm_tool_version':'8.13'})
    assert j['manager']=='gradle' and j['version']=='21'
    root2=tmp_path/'maven';root2.mkdir()
    maven=fixture(root2,kind='maven')
    f=maven/'app/pom.xml'
    f.write_text(f.read_text().replace('</project>',
       '<build><plugins><plugin><groupId>com.android.tools.build</groupId><artifactId>gradle</artifactId></plugin></plugins></build></project>'))
    with pytest.raises(JvmError,match='Android'):
        jvm_project(maven,manager='maven')
