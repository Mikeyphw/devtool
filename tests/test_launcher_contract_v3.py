"""Adversarial fixtures for the reusable Devtool Launcher Contract v3."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from devtool import launcher_contract_v3 as lc


def g(repo:Path,*a:str)->str:
    return subprocess.run(['git','-C',str(repo),*a],check=True,capture_output=True,text=True).stdout.strip()


def fixture(root:Path,name:str='sample')->Path:
    repo=root/name;repo.mkdir();g(repo,'init','-b','main');g(repo,'config','user.name','CI')
    g(repo,'config','user.email','ci@example.invalid')
    g(repo,'remote','add','origin',f'git@github.com:Mikeyphw/{name}.git')
    (repo/'original.txt').write_text('BEFORE\n')
    g(repo,'add','.');g(repo,'commit','-m','baseline')
    return repo


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_a01_sha_full_exact(self):
        self.assertEqual(lc.validate_sha('a'*40,'X'),'a'*40)
        for x in ['a'*39,'a'*41,'A'*40,'g'*40]:
            with self.assertRaises(lc.Refused):lc.validate_sha(x,'x')

    def test_a02_remote_variants(self):
        for v in ['git@github.com:Mikeyphw/devtool.git','https://github.com/Mikeyphw/devtool.git','ssh://git@github.com/Mikeyphw/devtool']:
            self.assertEqual(lc.canonical_remote(v),'Mikeyphw/devtool')
        for v in ['git@gitlab.com:Mikeyphw/devtool','https://token@github.com/Mikeyphw/devtool']:
            with self.assertRaises(lc.Refused):lc.canonical_remote(v)
        self.assertEqual(lc.canonical_remote('https://github.com/evil/other'),'evil/other')

    def test_a03_paths_traversal(self):
        for p in ['../x','a/../x','a//x','/etc/shadow','a\\b','a/.git/config','x/./y','']:
            with self.assertRaises(lc.Refused):lc.safe_path(p)
        self.assertEqual(lc.safe_path('docs/new.txt'),'docs/new.txt')

    def test_a04_source_identity_unchanged(self):
        root=fixture(self.root)
        a=lc.repo_identity(root,clean=True)
        self.assertEqual(a['source_sha'],g(root,'rev-parse','HEAD'))
        self.assertEqual(a['source_tree'],g(root,'rev-parse','HEAD^{tree}'))
        self.assertEqual(a['repository'],'Mikeyphw/sample')
        (root/'original.txt').write_text('DIRTY')
        with self.assertRaisesRegex(lc.Refused,'DIRTY_WORKTREE'):lc.repo_identity(root,clean=True)

    def test_a05_detached_branch_refused(self):
        root=fixture(self.root);g(root,'checkout','--detach')
        with self.assertRaises(lc.Refused):lc.repo_identity(root,clean=True)

    def test_a06_symlink_payload_refused(self):
        root=fixture(self.root);folder=self.root/'payload';folder.mkdir()
        (folder/'alias').symlink_to(root/'original.txt')
        with self.assertRaisesRegex(lc.Refused,'SYMLINK'):
            lc.payload_contract({'schema_version':1,'changes':[{'path':'alias','action':'modify'}]},folder,root)

    def test_a07_add_existing_refused(self):
        root=fixture(self.root);folder=self.root/'payload';folder.mkdir();(folder/'original.txt').write_text('X')
        with self.assertRaisesRegex(lc.Refused,'ALREADY_EXISTS'):
            lc.payload_contract({'schema_version':1,'changes':[{'path':'original.txt','action':'add'}]},folder,root)

    def test_a08_modify_missing_refused(self):
        root=fixture(self.root);folder=self.root/'payload';folder.mkdir();(folder/'missing.txt').write_text('X')
        with self.assertRaisesRegex(lc.Refused,'MISSING'):
            lc.payload_contract({'schema_version':1,'changes':[{'path':'missing.txt','action':'modify'}]},folder,root)

    def test_a09_replay_fails_closed(self):
        root=fixture(self.root);identity=lc.repo_identity(root,clean=True)
        manifest={'source':identity,'inventory':[{'path':'new.txt','action':'add','source_blob_sha':'','payload_sha256':'a'*64}]}
        (root/'new.txt').write_text('X')
        with self.assertRaisesRegex(lc.Refused,'DIRTY_WORKTREE'):
            lc.check_source(root,manifest)
        g(root,'add','new.txt');g(root,'commit','-m','change')
        with self.assertRaisesRegex(lc.Refused,'SOURCE_SOURCE_SHA_MISMATCH'):
            lc.check_source(root,manifest)

    def test_a10_replace_objects_does_not_shadow_tree(self):
        root=fixture(self.root)
        identity=lc.repo_identity(root,clean=True)
        # Git replacement objects affect object traversal, not HEAD string.
        other=self.root/'other';other.mkdir()
        newfile=root/'original.txt';newfile.write_text('CHANGED\n')
        g(root,'add','.');g(root,'commit','-m','new')
        second=g(root,'rev-parse','HEAD');first=identity['source_sha']
        g(root,'replace',first,second)
        # Reset only the disposable fixture to old commit (not a user repo).
        g(root,'checkout','--detach',first)
        self.assertEqual(lc.git(root,'rev-parse','HEAD^{tree}'),identity['source_tree'])

    def test_a11_payload_pins(self):
        root=fixture(self.root);folder=self.root/'payload';folder.mkdir()
        (folder/'original.txt').write_text('AFTER\n');(folder/'extra.txt').write_text('EXTRA\n')
        plan={'schema_version':1,'changes':[{'path':'original.txt','action':'modify'},{'path':'extra.txt','action':'add'}]}
        inv,data=lc.payload_contract(plan,folder,root)
        self.assertEqual(inv[0]['source_blob_sha'],g(root,'rev-parse','HEAD:original.txt'))
        self.assertEqual(inv[1]['source_blob_sha'],'')
        self.assertEqual(data['payload/extra.txt'],b'EXTRA\n')

    def test_a12_duplicate_inventory(self):
        root=fixture(self.root);folder=self.root/'payload';folder.mkdir();(folder/'new').write_text('A')
        plan={'schema_version':1,'changes':[{'path':'new','action':'add'}]*2}
        with self.assertRaisesRegex(lc.Refused,'DUPLICATE'):
            lc.payload_contract(plan,folder,root)

    def test_a13_zip_member_traversal(self):
        root=fixture(self.root);path=root/'evil.pyz'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('__main__.py','');z.writestr('manifest.json','{}')
            z.writestr('../secret','oops')
        with self.assertRaises(lc.Refused):lc.read_embedded_package(path)

    def test_a14_manifest_preimage_too_short(self):
        m=self.fake_manifest()
        m['inventory'][0]['source_blob_sha']='a'*39
        m['inventory'][0]['action']='modify'
        m['plan']['changes'][0]['action']='modify'
        with self.assertRaisesRegex(lc.Refused,'FULL_40'):lc.verify_manifest(m)

    def test_a15_wrong_release_claim_refused(self):
        m=self.fake_manifest();m['limitations']['release_qualified']=True
        with self.assertRaisesRegex(lc.Refused,'FORBIDDEN_QUALIFICATION'):
            lc.verify_manifest(m)

    def test_a16_zip_tamper_refused(self):
        m=self.fake_manifest()
        file={'__main__.py':lc.bootstrap_content(m['devtool']).encode(),
              'manifest.json':json.dumps(m).encode(),'payload/new':b'FAKE'}
        package=self.root/'x.pyz';package.write_bytes(lc.zip_bytes(file))
        with self.assertRaisesRegex(lc.Refused,'HASH_MISMATCH'):lc.read_embedded_package(package)

    def test_a17_output_exclusive_no_overwrite(self):
        path=self.root/'sample.pyz';lc.exclusive_write(path,b'first')
        with self.assertRaises(FileExistsError):lc.exclusive_write(path,b'second')
        self.assertEqual(path.read_bytes(),b'first')

    def test_a18_redaction(self):
        s='Authorization: Bearer abcdef\nhttps://foo:secret@github.com/x\ngithub_pat_'+'a'*48
        out=lc.redact(s)
        self.assertNotIn('secret',out);self.assertNotIn('abcdef',out);self.assertNotIn('github_pat_',out)

    def test_a19_native_failed_stage_diagnostic(self):
        with self.assertRaises(lc.Refused) as cm:
            lc.exec_cmd([sys.executable,'-c',"import sys;print('real error',file=sys.stderr);sys.exit(2)"],stage='native-demo')
        self.assertIn('native-demo',str(cm.exception));self.assertIn('real error',str(cm.exception))

    def test_a20_caught_native_timeout(self):
        with self.assertRaisesRegex(lc.Refused,'TIMED_OUT'):
            lc.exec_cmd([sys.executable,'-c','import time;time.sleep(2)'],stage='timeout',timeout=.03)

    def test_a21_reject_old_session_id(self):
        repo=fixture(self.root);dev=self.root/'devtool';dev.mkdir()
        with self.assertRaisesRegex(lc.Refused,'INVALID_SESSION_ID'):
            lc.session_check(repo,dev,'deadbeef')

    def test_a22_no_implicit_remote(self):
        p=lc.make_parser(False)
        a=p.parse_args(['apply','--repo','/tmp/example','--devtool-repo','/tmp/devtool'])
        self.assertFalse(a.approve_remote)
        with self.assertRaises(SystemExit):p.parse_args(['deliver','--repo','/tmp/example','--devtool-repo','/tmp/devtool'])

    def test_a23_bounded_ci(self):
        p=lc.make_parser(False)
        x=p.parse_args(['ci','--repo','/tmp/repo','--devtool-repo','/tmp/devtool','--session-id','a'*32])
        self.assertEqual(x.checks,25);self.assertEqual(x.interval,8)

    def test_a24_remote_only_native(self):
        m=self.fake_manifest();repo=fixture(self.root);dt=self.root/'devtool';dt.mkdir()
        with patch('devtool.launcher_contract_v3.session_check',return_value={'head_sha':g(repo,'rev-parse','HEAD')}),\
             patch('devtool.launcher_contract_v3.git',return_value=g(repo,'rev-parse','HEAD')),\
             patch('devtool.launcher_contract_v3.native') as call:
            lc.push_pr(repo,dt,m,'a'*32)
            self.assertEqual(call.call_count,2)
            self.assertEqual(call.call_args_list[0].args[2:5],('delivery','session','push'))
            self.assertEqual(call.call_args_list[1].args[2:5],('delivery','session','pr'))

    def test_a25_no_release_operations(self):
        self.assertFalse(any('release' in c or 'merge' in c or 'dispatch' in c for command in lc.CLI_CONTRACT for c in command))

    def test_a27_devtool_life_receipt_preserved(self):
        repo=fixture(self.root)
        folder=repo/'.devtool/private/cleanup/receipts';folder.mkdir(parents=True)
        path=folder/('cleanup-receipt-'+'f'*32+'.json')
        path.write_text(json.dumps({'receipt_id':'cleanup-receipt:'+'f'*32,'status':'applied'})+'\n')
        lc.verify_postcommit_status(repo)
        self.assertTrue(path.is_file())  # never delete Devtool provenance

    def test_a28_postcommit_unrelated_dirt_refused(self):
        repo=fixture(self.root)
        (repo/'unrelated.tmp').write_text('content')
        with self.assertRaisesRegex(lc.Refused,'UNREVIEWED_WORKTREE_DIRT'):
            lc.verify_postcommit_status(repo)

    def test_a26_deterministic_bytes(self):
        d={'__main__.py':b'abc','manifest.json':b'source'}
        self.assertEqual(lc.zip_bytes(d),lc.zip_bytes(dict(reversed(list(d.items())))))

    def fake_manifest(self):
        return {'schema':lc.SCHEMA,'name':'test',
                'source':{'repository':'Mikeyphw/sample','branch':'main','source_sha':'a'*40,'source_tree':'b'*40},
                'devtool':{'repository':'Mikeyphw/devtool','schema_blob':'c'*40, 'source_sha':'d'*40,'source_tree':'e'*40,'module_blob':'f'*40},
                'plan':{'schema_version':1,'changes':[{'path':'new','action':'add'}]},
                'inventory':[{'path':'new','action':'add','source_blob_sha':'','payload_sha256':'d'*64}],
                'delivery':{'base':'main','commit_message':'feat: sample','pr_title':'Sample','pr_body':''},
                'limitations':{'release_qualified':False,'device_qualified':False,'auto_merge_permitted':False,'local_gradle_permitted':False}}


class ContractV3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.m=ContractTests.fake_manifest(ContractTests)

    def test_v301_provenance_full_runtime_pins(self):
        lc.verify_manifest(self.m)
        for key in ('source_sha','source_tree','schema_blob','module_blob'):
            bad=json.loads(json.dumps(self.m));bad['devtool'][key]='a'*39
            with self.assertRaisesRegex(lc.Refused,'FULL_40'):lc.verify_manifest(bad)

    def test_v302_manifest_only_zipapp(self):
        package=self.root/'x.pyz'
        package.write_bytes(lc.zip_bytes({'__main__.py':lc.bootstrap_content(self.m['devtool']).encode(),
                                         'manifest.json':json.dumps(self.m).encode(),
                                         'payload/new':b'GOOD'}))
        with self.assertRaisesRegex(lc.Refused,'ZIP_PAYLOAD_HASH_MISMATCH'):lc.read_embedded_package(package)
        self.assertEqual(set(zipfile.ZipFile(package).namelist()),{'__main__.py','manifest.json','payload/new'})

    def test_v303_runtime_bootstrap_refuses_before_module_import(self):
        code=lc.bootstrap_content(self.m['devtool']);p=self.root/'cmd.py';p.write_text(code)
        cmd=subprocess.run([sys.executable,str(p),'check','--devtool-repo',str(self.root)],capture_output=True,text=True)
        self.assertNotEqual(cmd.returncode,0)
        self.assertIn('runtime source_sha drift',cmd.stderr)
        self.assertNotIn('No module named',cmd.stderr)

    def test_v304_bootstrap_pinned_source_tree(self):
        bootstrap=lc.bootstrap_content(self.m['devtool'])
        self.assertIn('HEAD^{tree}',bootstrap)
        self.assertIn(self.m['devtool']['source_sha'],bootstrap)
        self.assertIn(self.m['devtool']['source_tree'],bootstrap)
        self.assertNotIn('launcher_runtime.py',bootstrap)

    def test_v305_optional_native_commands_registered(self):
        p=lc.make_parser(False)
        cmds=('diagnose','capabilities','plan','verify-artifact','resume')
        for cmd in cmds:
            x=['--repo','/a','--devtool-repo','/b']
            if cmd=='resume':x+=['--session-id','f'*32]
            if cmd=='verify-artifact':x+=['--receipt','/tmp/x']
            self.assertEqual(p.parse_args([cmd,*x]).command,cmd)

    def _run(self,cmd:str,**attrs):
        m=json.loads(json.dumps(self.m))
        ns=argparse.Namespace(command=cmd,repo=str(self.root),devtool_repo=str(self.root),**attrs)
        mod='devtool.launcher_contract_v3.'
        with patch(mod+'read_embedded_package',return_value=(m,{'payload/new':b'GOOD'})),\
             patch(mod+'runtime_probe'),\
             patch(mod+'prepare_worktrees',side_effect=lambda a,b,m,**kw:(a,b)),\
             patch(mod+'native_json') as json_call,\
             patch(mod+'session_check',return_value={'state':'CI_PENDING'}),\
             patch(mod+'native') as native_call:
            yield ns,json_call,native_call

    def test_v306_check_runs_native_validation(self):
        ns=argparse.Namespace(command='check',repo=str(self.root),devtool_repo=str(self.root))
        with patch('devtool.launcher_contract_v3.read_embedded_package',return_value=(self.m,{})),\
             patch('devtool.launcher_contract_v3.check_source'),\
             patch('devtool.launcher_contract_v3.prepare_worktrees',side_effect=lambda a,b,m,**kw:(a,b)),\
             patch('devtool.launcher_contract_v3.runtime_probe'),\
             patch('devtool.launcher_contract_v3.scaffold',return_value=self.root/'verified.zip') as sc:
            self.assertEqual(lc.runtime_command(ns,self.root/'dummy.pyz'),0)
            sc.assert_called_once()

    def test_v307_plan_delegates_to_native_dag(self):
        for ns, json_call, native_call in self._run('plan',target='go',workflow='validate'):
            json_call.return_value={'graph':'valid'}
            self.assertEqual(lc.runtime_command(ns,self.root/'x.pyz'),0)
            self.assertEqual(json_call.call_args.args[2:4],('plan','validate'))
            self.assertEqual(json_call.call_args.args[4:6],('--target','go'))
            native_call.assert_not_called()

    def test_v308_diagnose_delegates_recovery_read_only(self):
        for ns,json_call,_ in self._run('diagnose'):
            json_call.return_value={'safe_to_auto_recover':False}
            self.assertEqual(lc.runtime_command(ns,self.root/'x.pyz'),0)
            self.assertEqual(json_call.call_args.args[2:4],('overlay','recovery-plan'))

    def test_v309_capabilities_native_only(self):
        for ns,json_call,_ in self._run('capabilities'):
            json_call.return_value={'supported':True}
            self.assertEqual(lc.runtime_command(ns,self.root/'x.pyz'),0)
            self.assertIn('capabilities',json_call.call_args.args)

    def test_v310_artifact_verify_delegates_exact_receipt(self):
        f=self.root/'artifact.json';f.write_text('{}')
        for ns,json_call,_ in self._run('verify-artifact',receipt=str(f),run_id=123,attempt=2):
            json_call.return_value={'verified':True}
            self.assertEqual(lc.runtime_command(ns,self.root/'x.pyz'),0)
            self.assertEqual(json_call.call_args.args[2:4],('artifacts','verify'))
            self.assertIn('--attempt',json_call.call_args.args)

    def test_v311_native_resume(self):
        for ns,json_call,_ in self._run('resume',session_id='a'*32):
            json_call.return_value={'state':'WAITING'}
            self.assertEqual(lc.runtime_command(ns,self.root/'x.pyz'),0)
            self.assertEqual(json_call.call_args.args[2:5],('delivery','session','resume'))

    def test_v312_non_green_ci_returns_nonzero(self):
        for ns,json_call,_ in self._run('ci',session_id='a'*32,checks=5,interval=1):
            json_call.return_value={'receipt':{'ci_state':'WAITING_REGISTRATION'}}
            self.assertEqual(lc.runtime_command(ns,self.root/'x.pyz'),3)
        for ns,json_call,_ in self._run('ci',session_id='a'*32,checks=5,interval=1):
            json_call.return_value={'receipt':{'ci_state':'REQUIRED_CHECKS_PASSED'}}
            self.assertEqual(lc.runtime_command(ns,self.root/'x.pyz'),0)

    def test_v313_reject_unsupported_plan_target(self):
        for ns,json_call,_ in self._run('plan',target='../../x',workflow='validate'):
            with self.assertRaisesRegex(lc.Refused,'INVALID_TARGET'):
                lc.runtime_command(ns,self.root/'x.pyz')
            json_call.assert_not_called()

    def test_v317_native_exec_uses_runtime_cwd_not_target(self):
        target=self.root/'feature';runtime=self.root/'devtool'
        target.mkdir();runtime.mkdir()
        with patch('devtool.launcher_contract_v3.exec_cmd',return_value='OK') as run:
            self.assertEqual(lc.native(target,runtime,'delivery','session','status',stage='check'),'OK')
            self.assertEqual(run.call_args.kwargs['cwd'],runtime)
            self.assertEqual(run.call_args.kwargs['target_root'],target)
            self.assertIn('--repo',run.call_args.args[0])
            self.assertIn(str(target),run.call_args.args[0])

    def test_v315_native_error_envelope_extracts_stderr(self):
        import subprocess as sp
        env={'ok':False,'exit_code':1,'stderr':'meaningful native failure',
             'unrelated':'x'*10000}
        with patch('devtool.launcher_contract_v3.subprocess.run',
                   return_value=sp.CompletedProcess(['native'],1,stdout=json.dumps(env),stderr='')):
            with self.assertRaises(lc.Refused) as cm:
                lc.exec_cmd(['native'],stage='envelope-check')
        self.assertIn('meaningful native failure',str(cm.exception))
        self.assertNotIn('unrelated',str(cm.exception))

    def test_v316_native_machine_envelope_failure_refused(self):
        with patch('devtool.launcher_contract_v3.native',return_value=json.dumps({'ok':False,'exit_code':1,'data':{'fake':'success'}})):
            with self.assertRaisesRegex(lc.Refused,'MACHINE_RECEIPT_NON_SUCCESS'):
                lc.native_json(self.root,self.root,'capabilities',stage='bad-json')

    def test_v314_wrong_runtime_commit_refused(self):
        repo=fixture(self.root,'devtool')
        (repo/'devtool').mkdir();(repo/'devtool/core_cli_schema.json').write_text('schema')
        (repo/'devtool/launcher_contract_v3.py').write_text('source')
        g(repo,'add','.');g(repo,'commit','-m','code')
        m=json.loads(json.dumps(self.m));m['devtool']['source_sha']='a'*40
        with patch('devtool.launcher_contract_v3.verify_postcommit_status'):
            with self.assertRaisesRegex(lc.Refused,'DEVTOOL_RUNTIME_SOURCE_SHA_DRIFT'):
                lc.runtime_probe(repo,repo,m)



def run():
    suite=unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.testsRun!=45:
        print('LAUNCHER_V3_TESTS_FAILED',flush=True);return 2
    print('LAUNCHER_V3_OFFLINE_ADVERSARIAL_TESTS_PASS count=45',flush=True)
    return 0

if __name__=='__main__':raise SystemExit(run())


class AutoWorktreeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.seed=self.root/'seed';self.seed.mkdir()
        g(self.seed,'init','-b','main')
        g(self.seed,'config','user.name','Test')
        g(self.seed,'config','user.email','test@example.invalid')
        self.bare=self.root/'remote.git'
        subprocess.run(['git','init','--bare','-b','main',str(self.bare)],check=True,capture_output=True)
        g(self.seed,'remote','add','origin',str(self.bare))
        (self.seed/'baseline.txt').write_text('first\n')
        g(self.seed,'add','.');g(self.seed,'commit','-m','first');g(self.seed,'push','origin','main')
        self.target=self.root/'target'
        subprocess.run(['git','clone',str(self.bare),str(self.target)],check=True,capture_output=True)
        g(self.target,'config','user.name','Test');g(self.target,'config','user.email','test@example.invalid')
        self.first=g(self.target,'rev-parse','HEAD')
        self.last=self.publish('second')
        original=lc.canonical_remote
        self.patcher=patch('devtool.launcher_contract_v3.canonical_remote',
                           side_effect=lambda x:('Mikeyphw/devtool' if x==str(self.bare) else original(x)))
        self.patcher.start();self.addCleanup(self.patcher.stop)
        self.state=patch.dict(os.environ,{'XDG_STATE_HOME':str(self.root/'private-state')})
        self.state.start();self.addCleanup(self.state.stop)

    def publish(self,name:str)->str:
        (self.seed/'baseline.txt').write_text(name+'\n');g(self.seed,'add','.')
        g(self.seed,'commit','-m',name);g(self.seed,'push','origin','main')
        return g(self.seed,'rev-parse','HEAD')

    def pin(self,sha:str|None=None)->dict:
        sha=sha or self.last
        return {'repository':'Mikeyphw/devtool','branch':'main',
                'source_sha':sha,'source_tree':g(self.seed,'rev-parse',sha+'^{tree}')}

    def test_ff01_no_upstream_required(self):
        # Exactly the user's gtpl failure: no upstream configured.
        g(self.target,'branch','--unset-upstream')
        lc._safe_fast_forward(self.target,self.pin(),label='target-source')
        self.assertEqual(g(self.target,'rev-parse','HEAD'),self.last)
        self.assertEqual(g(self.target,'symbolic-ref','--short','HEAD'),'main')

    def test_ff02_wrong_frozen_sha_keeps_head(self):
        fake=self.pin();fake['source_sha']='a'*40
        with self.assertRaisesRegex(lc.Refused,'REMOTE_NOT_PINNED'):
            lc._safe_fast_forward(self.target,fake,label='target-source')
        self.assertEqual(g(self.target,'rev-parse','HEAD'),self.first)

    def test_ff03_divergence_refuses_without_reset(self):
        (self.target/'baseline.txt').write_text('local diverged\n')
        g(self.target,'add','.');g(self.target,'commit','-m','diverged')
        current=g(self.target,'rev-parse','HEAD')
        with self.assertRaisesRegex(lc.Refused,'DIVERGED_OR_UNPROVEN'):
            lc._safe_fast_forward(self.target,self.pin(),label='target-source')
        self.assertEqual(g(self.target,'rev-parse','HEAD'),current)

    def test_ff04_dirty_refuses_without_fetch_or_change(self):
        (self.target/'private-note').write_text('keep me')
        with self.assertRaisesRegex(lc.Refused,'DIRTY_WORKTREE'):
            lc._safe_fast_forward(self.target,self.pin(),label='target-source')
        self.assertEqual(g(self.target,'rev-parse','HEAD'),self.first)
        self.assertEqual((self.target/'private-note').read_text(),'keep me')

    def test_ff05_disabled_refuses(self):
        with self.assertRaisesRegex(lc.Refused,'AUTO_FF_DISABLED'):
            lc._safe_fast_forward(self.target,self.pin(),label='target-source',enabled=False)
        self.assertEqual(g(self.target,'rev-parse','HEAD'),self.first)

    def test_ff06_runtime_cache_from_old_feature_no_branch_mutation(self):
        # Existing local clone is on an older feature branch with no upstream,
        # while the required Devtool runtime is the frozen merged main commit.
        g(self.target,'switch','-c','feature/old')
        old=g(self.target,'rev-parse','HEAD')
        consumer=fixture(self.root,'consumer')
        runtime=lc.resolve_runtime(consumer,self.target,self.pin())
        self.assertNotEqual(runtime,self.target)
        self.assertEqual(g(runtime,'rev-parse','HEAD'),self.last)
        self.assertEqual(g(self.target,'rev-parse','HEAD'),old)
        self.assertEqual(g(self.target,'symbolic-ref','--short','HEAD'),'feature/old')
        self.assertEqual(runtime,lc.resolve_runtime(consumer,self.target,self.pin()))

    def test_ff07_after_commit_no_rewind_during_session_status(self):
        g(self.target,'branch','--unset-upstream')
        lc._safe_fast_forward(self.target,self.pin(),label='target-source')
        pinned=self.pin()
        runtime=lc.resolve_runtime(self.target,self.target,pinned)
        (self.target/'after.txt').write_text('committed')
        g(self.target,'add','.');g(self.target,'commit','-m','apply completed')
        manifest={'source':pinned,'devtool':pinned,'inventory':[]}
        repo,bound=lc.prepare_worktrees(self.target,self.target,manifest,operation='status')
        self.assertEqual(bound,runtime)
        self.assertNotEqual(g(repo,'rev-parse','HEAD'),pinned['source_sha'])

    def test_ff08_no_reusing_corrupted_cached_runtime(self):
        lc._safe_fast_forward(self.target,self.pin(),label='target-source')
        runtime=lc.resolve_runtime(self.target,self.target,self.pin())
        (runtime/'intruder.txt').write_text('bad')
        with self.assertRaisesRegex(lc.Refused,'CACHED_DEVTOOL_RUNTIME_DRIFT'):
            lc.resolve_runtime(self.target,self.target,self.pin())
        self.assertEqual(g(self.target,'rev-parse','HEAD'),self.last)

    def test_ff09_no_automatic_feature_branch_promotion(self):
        g(self.target,'switch','-c','feature/old')
        feature=g(self.target,'rev-parse','HEAD')
        other=fixture(self.root,'consumer')
        lc.resolve_runtime(other,self.target,self.pin())
        self.assertEqual(g(self.target,'rev-parse','HEAD'),feature)
        self.assertEqual(g(self.target,'symbolic-ref','--short','HEAD'),'feature/old')

    def test_ff10_only_pinned_remote_main_fetch(self):
        wrong=self.pin();wrong['source_sha']='b'*40
        with self.assertRaisesRegex(lc.Refused,'REMOTE_MAIN_DRIFT'):
            lc.resolve_runtime(fixture(self.root,'consumer'),self.target,wrong)
        self.assertEqual(g(self.target,'rev-parse','HEAD'),self.first)


class ReplayFastForwardContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.repo=fixture(self.root,'devtool')
        self.before=g(self.repo,'rev-parse','HEAD')
        self.tree=g(self.repo,'rev-parse','HEAD^{tree}')
        self.manifest={
            'source':{'repository':'Mikeyphw/devtool','branch':'main',
                      'source_sha':self.before,'source_tree':self.tree},
            'devtool':{'repository':'Mikeyphw/devtool',
                       'source_sha':self.before,'source_tree':self.tree},
            'inventory':[{'path':'added.txt','action':'add','source_blob_sha':'','payload_sha256':'a'*64}],
        }
        (self.repo/'added.txt').write_text('now committed')
        g(self.repo,'add','.');g(self.repo,'commit','-m','applied')

    def test_ff11_replay_refuses_before_any_fetch(self):
        with patch('devtool.launcher_contract_v3._safe_fast_forward') as ff:
            with self.assertRaisesRegex(lc.Refused,'ALREADY_APPLIED_OR_CONFLICT'):
                lc.prepare_worktrees(self.repo,self.repo,self.manifest,operation='check')
            ff.assert_not_called()

    def test_ff12_advanced_no_replay_without_add_paths(self):
        self.manifest['inventory']=[]
        with patch('devtool.launcher_contract_v3._safe_fast_forward') as ff:
            with self.assertRaisesRegex(lc.Refused,'SOURCE_ALREADY_ADVANCED_NO_REPLAY'):
                lc.prepare_worktrees(self.repo,self.repo,self.manifest,operation='check')
            ff.assert_not_called()
