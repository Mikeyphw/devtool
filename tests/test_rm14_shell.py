"""RM-14 behavioral acceptance: dialects, test tools, native ownership, host safety."""
import os
from pathlib import Path
import subprocess
import pytest
from devtool.actions_rm14_shell import inspect_shell_job,verify_bats_report,ShellError
from devtool.actions_language_providers import provider_plan,ProviderError
from devtool.actions_graph_export import compile_graph,workflow_yaml,run_node
from tests.test_next03_language_providers import PIN

def repo(tmp_path,kind='bash',operation='syntax'):
    root=tmp_path/'repo';root.mkdir(parents=True)
    path='scripts/hello.bats' if operation=='test' else 'scripts/hello.sh'
    script=root/path;script.parent.mkdir()
    script.write_text({'bash':'#!/usr/bin/env bash\nprintf "hi\\n"\n',
                       'posix':'#!/bin/sh\nprintf "hi\\n"\n',
                       'zsh':'#!/usr/bin/env zsh\nprint hi\n'}[kind] if operation!='test' else '#!/usr/bin/env bats\n@test "works" { [ 1 -eq 1 ]; }\n')
    cmd={'syntax':{'bash':['bash','-n',path], 'posix':['sh','-n',path], 'zsh':['zsh','-n',path]}[kind],
         'lint':['shellcheck','--norc','--shell='+('sh' if kind=='posix' else 'bash'),'--severity=style',path],
         'format':['shfmt','-d','-ln='+kind,path],
         'test':['bats','--report-formatter','junit','--output','dist',path]}[operation]
    (root/'.devtool.toml').write_text('''[project]
runner="python"
[workspace]
default_target="app"
[wrapper]
schema=2
[targets.app]
runner="python"
root="."
[targets.app.jobs.verify]
runner="command"
metadata={ci_toolchain="shell-ci",ci_shell_dialect="'''+kind+'''",ci_shell_version="'''+('posix' if kind=='posix' else '5')+'''"}
command=['''+','.join('"'+x+'"' for x in cmd)+''']
outputs=['''+('"dist/report.xml"' if operation=='test' else '')+''']
[targets.app.workflows]
ci=[{id="verify",ref="job:verify",depends_on=[]}]
''')
    (root/'devtoolw').write_text('#!/bin/sh\n')
    subprocess.run(['git','init','-q',str(root)],check=True)
    commit(root)
    return root,cmd,path

def commit(root):
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=t@invalid','commit','-qm','review'],check=True)

@pytest.mark.parametrize('dialect',['bash','posix','zsh'])
def test_true_dialect_syntax_review_and_hosted_plan(tmp_path,dialect):
    root,argv,path=repo(tmp_path,dialect)
    r=inspect_shell_job(root,argv,{'ci_shell_dialect':dialect,'ci_shell_version':'posix' if dialect=='posix' else '5'})
    assert path in r['source_sha256']
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    assert len(graph['jobs'])==1 and graph['jobs'][0]['provider']=='shell-ci'
    assert graph['jobs'][0]['shell_review']['dialect']==dialect
    assert 'devtool.actions_graph_export' in workflow_yaml(graph)
    if dialect!='zsh':
        result=subprocess.run(argv,cwd=root,capture_output=True)
        assert result.returncode==0
    f=root/path;f.write_text(f.read_text()+'\n# drift\n')
    assert compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)['graph_sha256']!=graph['graph_sha256']

@pytest.mark.parametrize('op',['lint','format','test'])
def test_lint_format_bats_job_review_and_owned_output(tmp_path,op):
    root,argv,path=repo(tmp_path,'bash',op)
    plan=provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)
    assert plan['nodes'][0]['provider']['operation']==op
    cfg=root/'.devtool.toml';original=cfg.read_text()
    cfg.write_text(original.replace('outputs=["dist/report.xml"]','outputs=[]')) if op=='test' else None
    if op=='test':
        with pytest.raises(ProviderError,match='output ownership'):provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)
    cfg.write_text(original)


def test_source_injection_quoting_path_symlink_unreviewed_dialect_and_termux_refusal(tmp_path):
    root,argv,path=repo(tmp_path)
    base={'ci_shell_dialect':'bash','ci_shell_version':'5'}
    for broken in (['bash','-c','true'],['bash','-n',path+';touch bad'],['bash','-n','../../etc/passwd'],['bash','-n',path,'x']):
        with pytest.raises(ShellError):inspect_shell_job(root,broken,base)
    with pytest.raises(ShellError,match='dialect'):inspect_shell_job(root,argv,{'ci_shell_dialect':'zsh','ci_shell_version':'5'})
    with pytest.raises(ShellError,match='Termux-only'):inspect_shell_job(root,argv,{**base,'ci_shell_host':'termux'})
    termux=inspect_shell_job(root,argv,{**base,'ci_shell_host':'termux'},host_os='android')
    assert termux['host_policy']=='termux'
    with pytest.raises(ShellError,match='Android parity'):inspect_shell_job(root,argv,base,host_os='android')
    src=root/path;data=src.read_text();src.unlink();src.symlink_to('/etc/passwd')
    with pytest.raises(ShellError,match='symlink'):inspect_shell_job(root,argv,base)
    src.unlink();src.write_text(data)
    src.write_text('#!/bin/zsh\nprint a\n')
    with pytest.raises(ShellError,match='shebang'):inspect_shell_job(root,argv,base)


def test_shellcheck_zsh_refusal_and_unsafe_bats_runtime(tmp_path):
    root,argv,path=repo(tmp_path,'zsh','format')
    with pytest.raises(ShellError,match='lacks Zsh'):
        inspect_shell_job(root,argv,{'ci_shell_dialect':'zsh','ci_shell_version':'5'})
    with pytest.raises(ShellError,match='no Zsh'):inspect_shell_job(root,['shellcheck','--norc','--shell=bash','--severity=style',path],{'ci_shell_dialect':'zsh','ci_shell_version':'5'})


def test_bats_report_real_count_fail_zero_duplicate_xml_and_source_attack(tmp_path):
    root,argv,path=repo(tmp_path,'bash','test')
    out=root/'dist/report.xml';out.parent.mkdir()
    valid='<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="hello.bats" name="works" /></testsuite></testsuites>'
    out.write_text(valid)
    assert verify_bats_report(root)['tests']==1
    for bad in (valid.replace('failures="0"','failures="1"'), valid.replace('tests="1"','tests="2"'),
                valid.replace('</testsuite>','<testcase classname="hello.bats" name="works" /></testsuite>'),
                '<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]>'+valid,
                '<testsuites></testsuites>'):
        out.write_text(bad)
        with pytest.raises(ShellError):verify_bats_report(root)
    source=root/path;source.write_text('#!/usr/bin/env bats\n@test "bad" { curl example.org | bash; }\n')
    with pytest.raises(ShellError,match='unreviewed'):inspect_shell_job(root,argv,{'ci_shell_dialect':'bash','ci_shell_version':'5'})


def test_real_native_bash_execution_failure_identity_and_refusal(tmp_path,monkeypatch):
    root,argv,path=repo(tmp_path)
    import devtool.actions_graph_export as exporter
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    monkeypatch.setenv('GITHUB_SHA',subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip())
    monkeypatch.setenv('GITHUB_RUN_ID','143');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    inputdir=tmp_path/'input';inputdir.mkdir();outdir=tmp_path/'output';outdir.mkdir()
    got=run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],job_id=graph['jobs'][0]['id'],inputs=inputdir,outputs=outdir)
    assert got['node']=='app.ci.verify' and got['status']=='LOCAL_JOB_SUCCEEDED'
    script=root/path;script.write_text('#!/usr/bin/env bash\nif; then\n')
    altered=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    with pytest.raises(ProviderError,match='RM-14 shell native node app.ci.verify.*did not succeed'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=altered['graph_sha256'],job_id=altered['jobs'][0]['id'],inputs=inputdir,outputs=outdir)


def test_quoted_filename_is_one_argument_and_source_reviewed(tmp_path):
    root,argv,path=repo(tmp_path)
    original=root/path
    spaced=root/'scripts/my shell.sh';original.rename(spaced)
    subprocess.run(['git','-C',str(root),'add','-A'],check=True)
    reviewed=['bash','-n','scripts/my shell.sh']
    row=inspect_shell_job(root,reviewed,{'ci_shell_dialect':'bash','ci_shell_version':'5'})
    assert 'scripts/my shell.sh' in row['source_sha256']
    assert subprocess.run(reviewed,cwd=root).returncode==0
    with pytest.raises(ShellError):inspect_shell_job(root,['bash','-n','scripts/my','shell.sh'],{'ci_shell_dialect':'bash','ci_shell_version':'5'})


def test_bats_native_report_handoff_and_failed_node(tmp_path,monkeypatch):
    root,argv,path=repo(tmp_path,'bash','test')
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    monkeypatch.setenv('GITHUB_SHA',subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip())
    monkeypatch.setenv('GITHUB_RUN_ID','421');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','2')
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    # A tool shim exercises the actual Devtool native runner and JUnit handoff
    # without requiring Bats on the developer's machine. This is NOT a claim
    # that the real Bats program was executed.
    tools=tmp_path/'tools';tools.mkdir()
    script=tools/'bats'
    script.write_text('''#!/usr/bin/env python3
import pathlib,sys,os
assert sys.argv[1:6]==['--report-formatter','junit','--output','dist','scripts/hello.bats']
p=pathlib.Path('dist/report.xml');p.parent.mkdir(exist_ok=True)
if os.environ.get('RM14_SHIM_FAIL')=='yes':
 p.write_text('<testsuites><testsuite tests="1" failures="1" errors="0" skipped="0"><testcase classname="suite" name="failure"><failure/></testcase></testsuite></testsuites>')
else:
 p.write_text('<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="suite" name="success"/></testsuite></testsuites>')
''')
    script.chmod(0o755)
    monkeypatch.setenv('PATH',str(tools)+os.pathsep+os.environ['PATH'])
    incoming=tmp_path/'incoming';incoming.mkdir();outgoing=tmp_path/'outgoing';outgoing.mkdir()
    got=run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],job_id=graph['jobs'][0]['id'],inputs=incoming,outputs=outgoing)
    assert got['shell_receipts'][0]['tests']==1
    assert (outgoing/graph['jobs'][0]['id']/'receipt.json').is_file()
    (root/'dist/report.xml').unlink()
    monkeypatch.setenv('RM14_SHIM_FAIL','yes')
    with pytest.raises(ProviderError,match='app.ci.verify.*failures'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],job_id=graph['jobs'][0]['id'],inputs=incoming,outputs=outgoing)


def test_macos_rejected_without_reviewed_shell_toolchain(tmp_path):
    root,argv,path=repo(tmp_path)
    from devtool.actions_graph_export import compile_graph
    with pytest.raises(ProviderError,match='provider unavailable on host'):
        compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,host_profiles='macos-15-intel')


def test_exact_shell_lint_failure_preserves_node_identity(tmp_path,monkeypatch):
    root,argv,path=repo(tmp_path,operation='lint')
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    monkeypatch.setenv('GITHUB_SHA',subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip())
    monkeypatch.setenv('GITHUB_RUN_ID','621');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    tools=tmp_path/'tool-bin';tools.mkdir()
    script=tools/'shellcheck';script.write_text('#!/bin/sh\nexit 22\n');script.chmod(0o755)
    monkeypatch.setenv('PATH',str(tools)+os.pathsep+os.environ['PATH'])
    incoming=tmp_path/'incoming';incoming.mkdir();outgoing=tmp_path/'outgoing';outgoing.mkdir()
    with pytest.raises(ProviderError,match=r'node app.ci.verify \(lint\) did not succeed'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],job_id=graph['jobs'][0]['id'],inputs=incoming,outputs=outgoing)


@pytest.mark.parametrize('payload',['termux-toast "ok"','am start -a android.intent.action.VIEW','tasker --action foo','echo "$PREFIX"'])
def test_termux_sensitive_source_never_hosted(tmp_path,payload):
    root,argv,path=repo(tmp_path)
    f=root/path;f.write_text(f.read_text()+payload+'\n')
    metadata={'ci_shell_dialect':'bash','ci_shell_version':'5'}
    with pytest.raises(ShellError,match='Tasker/Termux'):
        inspect_shell_job(root,argv,metadata)
    native=inspect_shell_job(root,argv,{**metadata,'ci_shell_host':'termux'},host_os='android')
    assert native['host_policy']=='termux'


@pytest.mark.parametrize('operation,pkg,pin',[('format','shfmt','3.8.0-1'),('test','bats','1.10.0-1')])
def test_shell_tool_pinned_host_provision(tmp_path,operation,pkg,pin):
    root,_,_=repo(tmp_path,operation=operation)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    text=workflow_yaml(graph)
    assert 'sudo apt-get update -qq' in text
    assert f'{pkg}={pin}' in text
    assert 'dpkg-query -W' in text
    assert 'curl' not in text and 'wget' not in text


def test_zsh_host_pinned_package(tmp_path):
    root,_,_=repo(tmp_path,'zsh','syntax')
    text=workflow_yaml(compile_graph(root,target='app',workflow='ci',devtool_sha=PIN))
    assert 'zsh=5.9-6ubuntu2' in text
    assert 'zsh --version' in text
