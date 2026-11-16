"""Adversarial fixtures for the reusable Devtool Launcher Contract v3."""
from __future__ import annotations

import argparse
import contextlib
import io
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

    def test_hf01_detached_runtime_identity_optional_but_target_strict(self):
        root=fixture(self.root,'devtool')
        g(root,'checkout','--detach')
        identity=lc.repo_identity(root,clean=True,branch_required=False)
        self.assertEqual(identity['repository'],'Mikeyphw/devtool')
        self.assertEqual(identity['source_sha'],g(root,'rev-parse','HEAD'))
        with self.assertRaisesRegex(lc.Refused,'git-branch'):
            lc.repo_identity(root,clean=True)
        (root/'untracked').write_text('unreviewed')
        with self.assertRaisesRegex(lc.Refused,'DIRTY_WORKTREE'):
            lc.repo_identity(root,clean=True,branch_required=False)

    def test_hf02_detached_exact_runtime_probe_accepts_and_checks_blobs(self):
        root=fixture(self.root,'devtool')
        (root/'devtool').mkdir()
        (root/'devtool/__init__.py').write_text('')
        (root/'devtool/launcher_contract_v3.py').write_text('module-source\n')
        (root/'devtool/core_cli_schema.json').write_text('{}\n')
        g(root,'add','.');g(root,'commit','-m','exact pinned runtime')
        g(root,'checkout','--detach')
        actual=lc.repo_identity(root,clean=True,branch_required=False)
        manifest={'devtool':{'repository':'Mikeyphw/devtool',
                             'source_sha':actual['source_sha'],
                             'source_tree':actual['source_tree'],
                             'module_blob':g(root,'rev-parse','HEAD:devtool/launcher_contract_v3.py'),
                             'schema_blob':g(root,'rev-parse','HEAD:devtool/core_cli_schema.json')}}
        original=lc.exec_cmd
        def dispatch(argv, **kwargs):
            if kwargs.get('stage')=='runtime-source-import':
                return str((root/'devtool/__init__.py').resolve())+'\n'
            return original(argv,**kwargs)
        with patch.object(lc,'exec_cmd',side_effect=dispatch), patch.object(lc,'native') as native:
            lc.runtime_probe(root,root,manifest)
        self.assertEqual(native.call_count,2)
        bad=json.loads(json.dumps(manifest))
        bad['devtool']['module_blob']='a'*40
        with self.assertRaisesRegex(lc.Refused,'DEVTOOL_LAUNCHER_MODULE_DRIFT'):
            lc.runtime_probe(root,root,bad)

    def test_hf03_builder_accepts_detached_runtime_not_detached_target(self):
        runtime=fixture(self.root,'devtool')
        (runtime/'devtool').mkdir()
        (runtime/'devtool/launcher_contract_v3.py').write_text('module-source\n')
        (runtime/'devtool/core_cli_schema.json').write_text('{}\n')
        g(runtime,'add','.');g(runtime,'commit','-m','runtime source')
        g(runtime,'checkout','--detach')
        target=fixture(self.root,'consumer')
        payload=self.root/'payload';payload.mkdir()
        (payload/'added.txt').write_text('new payload\n')
        plan=self.root/'plan.json'
        plan.write_text(json.dumps({'schema_version':1,'changes':[{'path':'added.txt','action':'add'}]}))
        package=self.root/'hf03.pyz'
        args=argparse.Namespace(repo=str(target),devtool_repo=str(runtime),
             plan=str(plan),payload=str(payload),output=str(package),
             name='detached-runtime-hf03',commit_message='test: detached runtime',
             pr_title='Detached runtime',pr_body='fixture',base='main')
        lc.build_package(args)
        manifest,body=lc.read_embedded_package(package)
        self.assertEqual(manifest['devtool']['source_sha'],g(runtime,'rev-parse','HEAD'))
        self.assertEqual(body['payload/added.txt'],b'new payload\n')
        g(target,'checkout','--detach')
        args.output=str(self.root/'should-not-build.pyz')
        with self.assertRaisesRegex(lc.Refused,'git-branch'):
            lc.build_package(args)
        self.assertFalse(Path(args.output).exists())

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

    def test_hf04_detached_old_checkout_can_seed_pinned_runtime(self):
        g(self.target,'checkout','--detach')
        old=g(self.target,'rev-parse','HEAD')
        consumer=fixture(self.root,'consumer')
        runtime=lc.resolve_runtime(consumer,self.target,self.pin())
        self.assertNotEqual(runtime,self.target)
        self.assertEqual(g(runtime,'rev-parse','HEAD'),self.last)
        self.assertEqual(g(self.target,'rev-parse','HEAD'),old)
        self.assertNotEqual(subprocess.run(['git','-C',str(runtime),'symbolic-ref','-q','HEAD'],
                                             capture_output=True).returncode,0)

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


class ContractV31Tests(unittest.TestCase):
    """Concrete v3.1 opt-in/identity/presentation regression tests."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.manifest=ContractTests.fake_manifest(ContractTests)

    def test_3101_optional_explicit_paths_and_one_step_remote(self):
        p=lc.make_parser(False)
        run=p.parse_args(['apply','--approve-remote','--watch-ci'])
        self.assertIsNone(run.repo);self.assertIsNone(run.devtool_repo)
        self.assertTrue(run.approve_remote);self.assertTrue(run.watch_ci)
        self.assertEqual((run.checks,run.interval),(25,8))
        self.assertFalse(p.parse_args(['apply']).approve_remote)
        for item in ['status','report','ci','deliver','resume']:
            with self.assertRaises(SystemExit):p.parse_args([item])

    def test_3102_auto_target_exact_checkout(self):
        repo=fixture(self.root,'sample')
        self.manifest['source']['repository']='Mikeyphw/sample'
        self.manifest['source']['branch']='main'
        self.assertEqual(lc.resolve_target_path(None,self.manifest,cwd=repo),repo)
        nested=repo/'dir';nested.mkdir()
        self.assertEqual(lc.resolve_target_path(None,self.manifest,cwd=nested),repo)

    def test_3103_wrong_directory_refused_pre_apply(self):
        repo=fixture(self.root,'wrong')
        with self.assertRaisesRegex(lc.Refused,'AUTO_TARGET_WRONG_REPOSITORY'):
            lc.resolve_target_path(None,self.manifest,cwd=repo)

    def test_3104_discover_runtime_only_exact_trusted(self):
        repo=fixture(self.root,'devtool')
        pin=lc.repo_identity(repo,clean=True)
        other=fixture(self.root,'sample')
        with patch('devtool.launcher_contract_v3.runtime_candidates',return_value=[other,repo]):
            self.assertEqual(lc.discover_runtime(other,pin),repo)
        with patch('devtool.launcher_contract_v3.runtime_candidates',return_value=[other]):
            with self.assertRaisesRegex(lc.Refused,'NO_TRUSTED_DEVTOOL_CHECKOUT'):
                lc.discover_runtime(other,pin)

    def test_3105_native_ui_is_plain_even_with_parent_textual(self):
        with patch.dict(os.environ,{'DEVTOOL_EXECUTION_PRESENTATION':'textual',
                                     'DEVTOOL_JOB_UI':'compact',
                                     'DEVTOOL_TEXTUAL_APPLY_HOST':'1',
                                     'DEVTOOL_TEXTUAL_EXECUTION_HOST':'1'},clear=False):
            e=lc.safe_env()
        self.assertEqual(e['DEVTOOL_EXECUTION_PRESENTATION'],'plain')
        self.assertEqual(e['DEVTOOL_JOB_UI'],'plain')
        self.assertEqual(e['DEVTOOL_TEXTUAL_APPLY_HOST'],'0')
        self.assertEqual(e['DEVTOOL_TEXTUAL_EXECUTION_HOST'],'0')

    def test_3106_no_color_respected_in_native_process(self):
        with patch.dict(os.environ,{'NO_COLOR':'1','FORCE_COLOR':'', 'TERM':'xterm-256color'},clear=False):
            with patch('devtool.launcher_contract_v3.sys.stdout') as stream:
                stream.isatty.return_value=True
                e=lc.safe_env(color_output=True)
        self.assertEqual(e['NO_COLOR'],'1')
        self.assertEqual(e['FORCE_COLOR'],'')

    def test_3107_tty_force_color_only_when_allowed(self):
        with patch.dict(os.environ,{'TERM':'xterm-256color'},clear=True):
            with patch('devtool.launcher_contract_v3.sys.stdout') as stream:
                stream.isatty.return_value=True
                self.assertEqual(lc.safe_env(color_output=True)['FORCE_COLOR'],'1')
                stream.isatty.return_value=False
                self.assertNotIn('FORCE_COLOR',lc.safe_env(color_output=False))

    def test_3108_ci_watch_requires_remote_approval(self):
        args=lc.make_parser(False).parse_args(['apply','--watch-ci'])
        root=fixture(self.root,'sample')
        with patch('devtool.launcher_contract_v3.read_embedded_package',return_value=(self.manifest,{})), \
             patch('devtool.launcher_contract_v3.resolve_target_path',return_value=root), \
             patch('devtool.launcher_contract_v3.discover_runtime',return_value=root), \
             patch('devtool.launcher_contract_v3.prepare_worktrees',return_value=(root,root)), \
             patch('devtool.launcher_contract_v3.apply_package') as apply:
            with self.assertRaisesRegex(lc.Refused,'WATCH_CI_REQUIRES_APPROVE_REMOTE'):
                lc.runtime_command(args,self.root/'pkg.pyz')
            apply.assert_not_called()

    def test_3109_one_step_passes_native_watch_policy(self):
        args=lc.make_parser(False).parse_args(['apply','--approve-remote','--watch-ci','--checks','7','--interval','2'])
        root=fixture(self.root,'sample')
        with patch('devtool.launcher_contract_v3.read_embedded_package',return_value=(self.manifest,{})), \
             patch('devtool.launcher_contract_v3.resolve_target_path',return_value=root), \
             patch('devtool.launcher_contract_v3.discover_runtime',return_value=root), \
             patch('devtool.launcher_contract_v3.prepare_worktrees',return_value=(root,root)), \
             patch('devtool.launcher_contract_v3.apply_package',return_value=0) as apply:
            self.assertEqual(lc.runtime_command(args,self.root/'pkg.pyz'),0)
            self.assertEqual(apply.call_args.kwargs,dict(watch_ci=True,checks=7,interval=2))
            self.assertTrue(apply.call_args.args[-1])

    def test_3110_generated_bootstrap_autodiscovers_and_imports_exact(self):
        target=fixture(self.root,'sample')
        dev=fixture(self.root,'devtool')
        (dev/'devtool').mkdir()
        (dev/'devtool/__init__.py').write_text('')
        (dev/'devtool/launcher_contract_v3.py').write_text(
            "import os,sys\n"
            "def main():\n"
            " print('V31_STUB_ARGS',repr(sys.argv[1:]))\n"
            " print('V31_STUB_UI',os.getenv('DEVTOOL_EXECUTION_PRESENTATION'))\n"
            " return 0\n")
        (dev/'devtool/core_cli_schema.json').write_text('{}\n')
        g(dev,'add','.');g(dev,'commit','-m','native fixture')
        pins=lc.repo_identity(dev,clean=True)
        pins['module_blob']=g(dev,'rev-parse','HEAD:devtool/launcher_contract_v3.py')
        manifest={'source':lc.repo_identity(target,clean=True)}
        pkg=self.root/'v31.pyz'
        pkg.write_bytes(lc.zip_bytes({'__main__.py':lc.bootstrap_content(pins).encode(),
                 'manifest.json':json.dumps(manifest).encode()}))
        env=dict(os.environ,DEVTOOL_REPO=str(dev),PYTHONDONTWRITEBYTECODE='1')
        run=subprocess.run([sys.executable,'-B',str(pkg),'apply','--approve-remote'],
                           cwd=target,env=env,text=True,capture_output=True,timeout=30)
        self.assertEqual(run.returncode,0,run.stderr+'\n'+run.stdout)
        self.assertIn('V31_STUB_ARGS',run.stdout)
        self.assertIn("'--repo'",run.stdout)
        self.assertIn("'--devtool-repo'",run.stdout)
        self.assertIn("'--approve-remote'",run.stdout)
        self.assertIn('V31_STUB_UI plain',run.stdout)
        # The same archive must not import Devtool or mutate Git from wrong cwd.
        alien=fixture(self.root,'alien')
        bad=subprocess.run([sys.executable,'-B',str(pkg),'apply','--approve-remote'],
                           cwd=alien,env=env,text=True,capture_output=True,timeout=30)
        self.assertNotEqual(bad.returncode,0)
        self.assertIn('cd into exact target worktree',bad.stderr)
        self.assertNotIn('V31_STUB_ARGS',bad.stdout)


    def test_hf05_generated_zipapp_imports_detached_runtime(self):
        target=fixture(self.root,'sample')
        dev=fixture(self.root,'devtool')
        (dev/'devtool').mkdir()
        (dev/'devtool/__init__.py').write_text('')
        (dev/'devtool/launcher_contract_v3.py').write_text(
            "import sys\n"
            "def main():\n"
            " print('HF05_DETACHED_IMPORT_PASS',repr(sys.argv[1:]))\n"
            " return 0\n")
        (dev/'devtool/core_cli_schema.json').write_text('{}\n')
        g(dev,'add','.');g(dev,'commit','-m','detached native runtime')
        pins=lc.repo_identity(dev,clean=True)
        pins['module_blob']=g(dev,'rev-parse','HEAD:devtool/launcher_contract_v3.py')
        manifest={'source':lc.repo_identity(target,clean=True)}
        pkg=self.root/'hf05.pyz'
        pkg.write_bytes(lc.zip_bytes({'__main__.py':lc.bootstrap_content(pins).encode(),
             'manifest.json':json.dumps(manifest).encode()}))
        g(dev,'checkout','--detach')
        env=dict(os.environ,DEVTOOL_REPO=str(dev),PYTHONDONTWRITEBYTECODE='1')
        run=subprocess.run([sys.executable,'-B',str(pkg),'apply','--approve-remote'],
                           cwd=target,env=env,text=True,capture_output=True,timeout=30)
        self.assertEqual(run.returncode,0,run.stderr+'\n'+run.stdout)
        self.assertIn('HF05_DETACHED_IMPORT_PASS',run.stdout)
        self.assertIn("'--devtool-repo'",run.stdout)
        self.assertIn(str(dev),run.stdout)


class NativeDeliveryClipboardAndCITests(unittest.TestCase):
    """Current native-path constraints; no historical/phase-gate wrappers."""

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.manifest=ContractTests.fake_manifest(ContractTests)
        self.manifest['source']['repository']='Mikeyphw/sample'
        self.repo=fixture(self.root,'sample')
        self.runtime=self.root/'devtool-runtime';self.runtime.mkdir()

    def test_native_cli_always_sets_supported_no_clipboard(self):
        with patch('devtool.launcher_contract_v3.exec_cmd',return_value='') as run:
            lc.native(self.repo,self.runtime,'apply','overlay','pkg.zip',
                      '--yes',stage='native-atomic-apply',live=True)
        argv=run.call_args.args[0]
        self.assertEqual(argv[:8],[sys.executable,'-B','-m','devtool','--repo',
                                   str(self.repo),'--no-clipboard','apply'])
        self.assertEqual(argv[-3:],['overlay','pkg.zip','--yes'])
        self.assertTrue(run.call_args.kwargs['live'])

    def test_native_machine_receipts_also_disable_clipboard(self):
        with patch('devtool.launcher_contract_v3.exec_cmd',return_value='{"ok":true}') as run:
            self.assertEqual(lc.native_json(self.repo,self.runtime,'delivery','session',
                                            'status','--id','a'*32,stage='session-status'),
                             {'ok':True})
        argv=run.call_args.args[0]
        self.assertEqual(argv[6],'--no-clipboard')
        self.assertEqual(argv[-1],'--json')
        self.assertFalse(run.call_args.kwargs['live'])

    def test_policy_preflight_uses_native_committed_policy_authority(self):
        data={'policy_path':'config/delivery-required-ci.json',
              'policy_sha256':'a'*64,
              'required_workflows':[{'name':'Current CI','workflow_id':1234}],
              'release_qualified':False}
        with patch('devtool.launcher_contract_v3.exec_cmd',return_value=json.dumps(data)) as run:
            self.assertEqual(lc.required_ci_preflight(self.repo,self.runtime,
                                                     self.manifest)['policy_sha256'],'a'*64)
        argv=run.call_args.args[0]
        self.assertEqual(argv[:3],[sys.executable,'-B','-c'])
        self.assertIn('from devtool.delivery_ci_policy import load_policy',argv[3])
        self.assertIn('p.receipt()',argv[3])
        self.assertEqual(argv[4:],[str(self.repo),'Mikeyphw/sample','main'])
        self.assertEqual(run.call_args.kwargs['stage'],'native-required-ci-preflight')

    def test_policy_preflight_invalid_receipts_fail_closed(self):
        for payload in ('not json','{}',json.dumps({'required_workflows':[],
                                                   'policy_sha256':'a'*64}),
                        json.dumps({'required_workflows':[{'name':'Bad','workflow_id':0}],
                                    'policy_sha256':'a'*64})):
            with self.subTest(payload=payload), \
                 patch('devtool.launcher_contract_v3.exec_cmd',return_value=payload):
                with self.assertRaisesRegex(lc.Refused,'PREFLIGHT'):
                    lc.required_ci_preflight(self.repo,self.runtime,self.manifest)

    def test_missing_policy_prevents_scaffold_commit_and_remote_delivery(self):
        with patch('devtool.launcher_contract_v3.check_source'), \
             patch('devtool.launcher_contract_v3.runtime_probe'), \
             patch('devtool.launcher_contract_v3.required_ci_preflight',
                   side_effect=lc.Refused('NATIVE_COMMAND_FAILED stage=native-required-ci-preflight rc=1')), \
             patch('devtool.launcher_contract_v3.scaffold') as scaffold, \
             patch('devtool.launcher_contract_v3.native') as native, \
             patch('devtool.launcher_contract_v3.push_pr') as push:
            with self.assertRaisesRegex(lc.Refused,'native-required-ci-preflight'):
                lc.apply_package(self.repo,self.runtime,self.manifest,{},True,watch_ci=True)
            scaffold.assert_not_called();native.assert_not_called();push.assert_not_called()

    def test_local_policy_onboarding_avoids_ci_policy_gate(self):
        sentinel=lc.Refused('NEXT_STAGE_NATIVE_SCAFFOLD')
        with patch('devtool.launcher_contract_v3.check_source'), \
             patch('devtool.launcher_contract_v3.runtime_probe'), \
             patch('devtool.launcher_contract_v3.required_ci_preflight') as policy, \
             patch('devtool.launcher_contract_v3.scaffold',side_effect=sentinel) as scaffold:
            with self.assertRaisesRegex(lc.Refused,'NEXT_STAGE_NATIVE_SCAFFOLD'):
                lc.apply_package(self.repo,self.runtime,self.manifest,{},False,watch_ci=False)
            policy.assert_not_called();scaffold.assert_called_once()

    def test_real_policy_subprocess_error_is_diagnostic_and_safe(self):
        # The actual native policy checker is imported by a read-only Python
        # child, not approximated by the launcher. Missing module never passes.
        with self.assertRaisesRegex(lc.Refused,'native-required-ci-preflight'):
            lc.required_ci_preflight(self.repo,self.runtime,self.manifest)


class FailureOnlyFullLogTests(unittest.TestCase):
    """One current contract for failure-only full launcher+native diagnostics."""

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def log_files(self):
        return list((self.root/'.local/state/devtool/launcher-logs').glob('*.log'))

    def test_success_has_no_clipboard_suggestion_or_retained_log(self):
        stdout=io.StringIO();stderr=io.StringIO()
        with patch.dict(os.environ,{'HOME':str(self.root)}), \
             patch('devtool.launcher_contract_v3._RICH',None), \
             contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            self.assertEqual(lc._with_failure_transcript(lambda: (lc.say('SUCCESS'),0)[1]),0)
        self.assertEqual(self.log_files(),[])
        self.assertNotIn('COPY_SUGGESTION',stdout.getvalue()+stderr.getvalue())

    def test_failure_suggests_manual_copy_and_contains_full_native_streams(self):
        stdout=io.StringIO();stderr=io.StringIO()
        def failing():
            lc.say('LAUNCHER_SOURCE_AND_CONTRACT_CHECK')
            with self.assertRaisesRegex(lc.Refused,'stage=native-validation'):
                lc.exec_cmd([sys.executable,'-B','-c',
                    "import sys;print('NATIVE_STDOUT_123');print('NATIVE_STDERR_456',file=sys.stderr);sys.exit(9)"],
                    stage='native-validation',live=True)
            return 2
        with patch.dict(os.environ,{'HOME':str(self.root)}), \
             patch('devtool.launcher_contract_v3._RICH',None), \
             contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            self.assertEqual(lc._with_failure_transcript(failing),2)
        logs=self.log_files();self.assertEqual(len(logs),1)
        text=logs[0].read_text()
        self.assertIn('LAUNCHER_SOURCE_AND_CONTRACT_CHECK',text)
        self.assertIn('NATIVE_STDOUT_123',text)
        self.assertIn('NATIVE_STDERR_456',text)
        self.assertIn('LAUNCHER_NATIVE_BEGIN stage=native-validation',text)
        self.assertIn('termux-clipboard-set < ',stderr.getvalue())
        self.assertNotIn('copied to clipboard',stderr.getvalue())
        self.assertEqual(logs[0].stat().st_mode&0o077,0)
        self.assertEqual(logs[0].parent.stat().st_mode&0o077,0)

    def test_captured_contract_diagnostics_not_truncated_in_log(self):
        long_error='CONTRACT_FAILED_DIAGNOSTICS_'+'Z'*9500
        stdout=io.StringIO();stderr=io.StringIO()
        def failing():
            with self.assertRaisesRegex(lc.Refused,'native-contract'):
                lc.exec_cmd([sys.executable,'-B','-c',
                    'import sys;sys.stderr.write('+repr(long_error)+');sys.exit(3)'],
                    stage='native-contract')
            return 2
        with patch.dict(os.environ,{'HOME':str(self.root)}), \
             contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            self.assertEqual(lc._with_failure_transcript(failing),2)
        logs=self.log_files();self.assertEqual(len(logs),1)
        self.assertIn(long_error,logs[0].read_text())

    def test_disabled_log_does_not_offer_fictitious_copy_command(self):
        stdout=io.StringIO();stderr=io.StringIO()
        with patch('devtool.launcher_contract_v3._new_failure_transcript',return_value=None), \
             contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            self.assertEqual(lc._with_failure_transcript(lambda:2),2)
        self.assertNotIn('COPY_SUGGESTION',stdout.getvalue()+stderr.getvalue())


class MachineReceiptColorTests(unittest.TestCase):
    def test_3111_machine_capture_never_forces_color(self):
        with patch.dict(os.environ,{'FORCE_COLOR':'1','TERM':'xterm-256color'},clear=True):
            with patch('devtool.launcher_contract_v3.sys.stdout') as stream:
                stream.isatty.return_value=True
                machine=lc.safe_env(color_output=False)
                live=lc.safe_env(color_output=True)
        self.assertNotIn('FORCE_COLOR',machine)
        self.assertEqual(machine['NO_COLOR'],'1')
        self.assertEqual(live['FORCE_COLOR'],'1')
        self.assertNotIn('NO_COLOR',live)
