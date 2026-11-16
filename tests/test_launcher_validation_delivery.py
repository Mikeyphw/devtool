"""Devtool source-owned validation-aware launcher contract and negative cases."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile
import tempfile
from unittest.mock import patch
import unittest

from devtool.launcher_validation_contract import (
    ValidationContractError, inspect_delivery_plan, metadata_descriptors,
    validation_execution_mode, verify_metadata_manifest,
)
from devtool import launcher_contract_v3 as launcher
from devtool import delivery_adoption as adoption


def plan(script=b'print("ok")\n', mode='full'):
    return ({'schema_version':1,'changes':[{'path':'README.md','action':'modify'}],
             'delivery_validation_mode':mode,
             'validation':{'mode':'required','required':True,'restore':False,'build':False,
                           'test':False,'script':{'path':'.devtool-artifact/validate.py',
                           'runner':'python3','sha256':hashlib.sha256(script).hexdigest(),
                           'timeout_seconds':90},'rollback_on_failure':True}},script)


class ValidationAwareDeliveryContractTests(unittest.TestCase):
    def test_legacy_scaffold_plan_preserved(self):
        self.assertEqual(inspect_delivery_plan({'schema_version':1,'changes':[]}), ({},{}))
        self.assertEqual(validation_execution_mode({'schema_version':1,'changes':[]}), 'static')

    def test_full_exec_validation_opt_in(self):
        p, data=plan()
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'.devtool-artifact/validate.py';path.parent.mkdir()
            path.write_bytes(data)
            config,files=inspect_delivery_plan(p,Path(d))
        self.assertEqual(config['delivery_validation_mode'],'full')
        self.assertEqual(files['.devtool-artifact/validate.py'],data)
        verify_metadata_manifest(p,metadata_descriptors(files))

    def test_declared_tests_cannot_be_skipped_by_static(self):
        p,_=plan(mode='static')
        p['delivery_static_justification']='Static checks only; validation tests must not be skipped.'
        p['validation']['tests']=[{'id':'contracts','runner':'python','command':['python3','-m','unittest']}]
        with self.assertRaisesRegex(ValidationContractError,'silently skip'):
            inspect_delivery_plan(p)

    def test_full_requires_explicit_executable_policy(self):
        p,_=plan()
        p['validation']={'required':True,'restore':False,'build':False,'test':False}
        with self.assertRaisesRegex(ValidationContractError,'zero executable'):
            inspect_delivery_plan(p)
        del p['validation']
        with self.assertRaisesRegex(ValidationContractError,'requires explicit'):
            inspect_delivery_plan(p)

    def test_deferred_refused_until_native_recovery_implemented(self):
        p,_=plan();p['delivery_validation_mode']='deferred'
        with self.assertRaisesRegex(ValidationContractError,'deferred'):
            inspect_delivery_plan(p)

    def test_untrusted_fields_and_bypass_refused(self):
        p,_=plan();p['shell_command']='rm -rf .'
        with self.assertRaisesRegex(ValidationContractError,'Unknown'):
            inspect_delivery_plan(p)
        del p['shell_command']
        p['validation']['required']=False
        with self.assertRaisesRegex(ValidationContractError,'cannot be false'):
            inspect_delivery_plan(p)
        p['validation']['required']=True
        p['validation']['mode']='disabled'
        with self.assertRaisesRegex(ValidationContractError,'disabled'):
            inspect_delivery_plan(p)

    def test_reject_hook_traversal_and_inventory_collision(self):
        p,_=plan()
        p['validation']['script']['path']='../escape.py'
        with self.assertRaises(ValidationContractError):inspect_delivery_plan(p)
        p['validation']['script']['path']='README.md'
        with self.assertRaises(ValidationContractError):inspect_delivery_plan(p)

    def test_hook_bytes_and_sha_refused(self):
        p,_=plan()
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'.devtool-artifact/validate.py';path.parent.mkdir()
            path.write_text('tampered')
            with self.assertRaisesRegex(ValidationContractError,'digest'):
                inspect_delivery_plan(p,Path(d))

    def test_hook_symlinks_refused(self):
        p,data=plan()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'real.py').write_bytes(data)
            (root/'.devtool-artifact').symlink_to(root)
            with self.assertRaisesRegex(ValidationContractError,'symlink'):
                inspect_delivery_plan(p,root)

    def test_metadata_missing_or_duplicated_refused(self):
        p,_=plan()
        with self.assertRaises(ValidationContractError):verify_metadata_manifest(p,[])
        x={'path':'.devtool-artifact/validate.py','sha256':p['validation']['script']['sha256'],'size':12}
        with self.assertRaises(ValidationContractError):verify_metadata_manifest(p,[x,x])
        verify_metadata_manifest(p,[x])

    def test_static_with_validator_remains_explicit(self):
        p,_=plan(mode='static')
        p['delivery_static_justification'] = 'Target runner unavailable; static contract validator only.'
        self.assertEqual(validation_execution_mode(p),'static')

    def test_static_cannot_silently_skip_restore_build_test(self):
        for lifecycle in ('restore', 'build', 'test'):
            p, _ = plan(mode='static')
            p['delivery_static_justification'] = 'Android runner unavailable; validator checks contracts only.'
            p['validation'][lifecycle] = True
            with self.subTest(lifecycle=lifecycle), self.assertRaisesRegex(ValidationContractError, 'silently skip'):
                inspect_delivery_plan(p)

    def test_static_requires_justification_validator_and_explicit_skip_flags(self):
        p, _ = plan(mode='static')
        with self.assertRaisesRegex(ValidationContractError, 'justification'):
            inspect_delivery_plan(p)
        p['delivery_static_justification'] = 'Host cannot execute target builds; checks are limited.'
        self.assertEqual(validation_execution_mode(p), 'static')
        del p['validation']['script']
        with self.assertRaises(ValidationContractError):
            inspect_delivery_plan(p)
        p, _ = plan(mode='static')
        p['delivery_static_justification'] = 'Only static hooks are available for this target.'
        del p['validation']['build']
        with self.assertRaisesRegex(ValidationContractError, 'explicitly disable'):
            inspect_delivery_plan(p)

    def test_malformed_and_optional_only_tests_refused(self):
        p, _ = plan()
        del p['validation']['script']
        for declaration in ([42], [{'id':'x','runner':'python','command':[]}],
                            [{'id':'x','runner':'python','command':['python3'], 'required':'yes'}]):
            p['validation']['tests'] = declaration
            with self.subTest(declaration=declaration), self.assertRaises(ValidationContractError):
                inspect_delivery_plan(p)
        p['validation']['tests'] = [{'id':'x','runner':'python','command':['python3'], 'required':False}]
        with self.assertRaisesRegex(ValidationContractError, 'optional tests'):
            inspect_delivery_plan(p)

    def test_builder_requires_explicit_legacy_static_permission(self):
        import ast
        from pathlib import Path
        # This source-owned gate asserts the native builder now has an explicit
        # legacy-compatibility override and no default static-only acceptance.
        src = Path(launcher.__file__).read_text(encoding='utf-8')
        ast.parse(src)
        self.assertIn("NEW_PACKAGE_REQUIRES_EXECUTABLE_VALIDATION",src)
        parser = launcher.make_parser(True)
        args = parser.parse_args(['build', '--repo','/tmp/repo','--devtool-repo','/tmp/devtool',
                                  '--plan','/tmp/plan','--payload','/tmp/payload','--output','/tmp/out.pyz',
                                  '--name','fixture','--commit-message','fixture','--pr-title','fixture'])
        self.assertFalse(args.allow_legacy_static)

    def test_exclusive_tasks_cannot_carry_unexecuted_validator(self):
        p,_=plan()
        p['validation']['tasks']=['test']
        with self.assertRaisesRegex(ValidationContractError,'Exclusive tasks'):
            inspect_delivery_plan(p)

    def test_exclusive_tasks_refuse_promised_standard_lifecycle(self):
        p, _ = plan()
        del p['validation']['script']
        p['validation']['tasks'] = ['test']
        p['validation']['build'] = True
        with self.assertRaisesRegex(ValidationContractError, 'Exclusive tasks'):
            inspect_delivery_plan(p)

    def test_zipapp_budget_includes_chained_native_hook_metadata(self):
        # Native package ceiling must account for metadata-only hook bytes,
        # not just the 6MiB source inventory. This previously refused valid
        # archives despite the per-hook and aggregate contracts passing.
        from devtool.launcher_validation_contract import MAX_TOTAL_HOOK_BYTES
        hook_bytes = {'.devtool-artifact/'+part+'.py':b'#'+b'x'*119999
                      for part in ('validate','preflight','finalize')}
        assert sum(map(len,hook_bytes.values())) > 300000
        assert sum(map(len,hook_bytes.values())) <= MAX_TOTAL_HOOK_BYTES
        payload = {'payload/src/file'+str(i)+'.txt':bytes([65+i])*(2*1024*1024)
                   for i in range(3)}
        inventory = [{'path':p.removeprefix('payload/'),'action':'add','source_blob_sha':'',
                      'payload_sha256':hashlib.sha256(b).hexdigest()} for p,b in payload.items()]
        target = {'repository':'Mikeyphw/devtool','branch':'feature/size-budget',
                  'source_sha':'a'*40,'source_tree':'b'*40}
        runtime = {'repository':'Mikeyphw/devtool','source_sha':'c'*40,
                   'source_tree':'d'*40,'module_blob':'e'*40,'launcher_version':'3.2.0'}
        manifest = launcher.make_manifest('size-budget-fixture',target,runtime,inventory,
                     'native budget test','Native budget test','','main','f'*40)
        manifest['plan']['delivery_validation_mode']='full'
        manifest['plan']['validation']={'required':True,'restore':False,'build':False,'test':False,
             'script':{'path':'.devtool-artifact/validate.py','runner':'python3',
                       'sha256':hashlib.sha256(hook_bytes['.devtool-artifact/validate.py']).hexdigest()}}
        for kind in ('preflight','finalize'):
            path='.devtool-artifact/'+kind+'.py'
            manifest['plan'][kind]={'script':{'path':path,'runner':'python3',
                 'sha256':hashlib.sha256(hook_bytes[path]).hexdigest()}}
        manifest['artifact_metadata']=metadata_descriptors(hook_bytes)
        launcher.verify_manifest(manifest)
        files={'__main__.py':launcher.bootstrap_content(manifest['devtool']).encode(),
               'manifest.json':(json.dumps(manifest)+'\n').encode(),**payload,
               **{'payload/'+p:b for p,b in hook_bytes.items()}}
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'fixture.pyz'
            path.write_bytes(launcher.zip_bytes(files))
            self.assertGreater(sum(len(blob) for blob in files.values()), launcher.MAX_TOTAL+300000)
            parsed, content=launcher.read_embedded_package(path)
        self.assertEqual(parsed,manifest)
        self.assertEqual(content['payload/.devtool-artifact/finalize.py'],
                         hook_bytes['.devtool-artifact/finalize.py'])

    def test_native_scaffold_carries_hook_metadata_without_repository_copy(self):
        # Exercise the real S7 generator on an isolated Git fixture. The hook
        # remains metadata and is not executed by the scaffold itself.
        with tempfile.TemporaryDirectory() as d:
            t=Path(d);root=t/'consumer';root.mkdir()
            def git(*args):
                return subprocess.run(['git','-C',str(root),*args],check=True,
                                      capture_output=True,text=True).stdout.strip()
            git('init','-b','feature/validation-fixture')
            git('config','user.email','tests@example.invalid')
            git('config','user.name','Fixture')
            git('remote','add','origin','git@github.com:Mikeyphw/validation-fixture.git')
            (root/'README.md').write_text('before\n')
            git('add','README.md');git('commit','-m','fixture')
            head=git('rev-parse','HEAD')
            payload=t/'payload';(payload/'.devtool-artifact').mkdir(parents=True)
            (payload/'README.md').write_text('after\n')
            spec,raw=plan()
            (payload/'.devtool-artifact/validate.py').write_bytes(raw)
            manifest=t/'plan.json';manifest.write_text(json.dumps(spec))
            archive=t/'artifact.zip'
            receipt=adoption.scaffold_overlay(root,payload,manifest,archive,expected_head=head)
            self.assertFalse(receipt['applied'])
            self.assertEqual(git('rev-parse','HEAD'),head)
            self.assertEqual((root/'README.md').read_text(),'before\n')
            with zipfile.ZipFile(archive) as z:
                native=json.loads(z.read('.devtool-artifact.json'))
                self.assertTrue(native['validation']['required'])
                self.assertEqual(z.read('.devtool-artifact/validate.py'),raw)
                self.assertEqual([x['path'] for x in native['inventory']['entries']],['README.md'])
                self.assertEqual(z.testzip(),None)
            spec['delivery_validation_mode']='static'
            spec['delivery_static_justification']='Target lacks native build runner; static contract verification only.'
            manifest.write_text(json.dumps(spec))
            static_archive=t/'artifact-static.zip'
            adoption.scaffold_overlay(root,payload,manifest,static_archive,expected_head=head)
            with zipfile.ZipFile(static_archive) as z:
                static_manifest=json.loads(z.read('.devtool-artifact.json'))
            self.assertEqual(static_manifest['notes'],[
                'Explicit static-only validation: '+spec['delivery_static_justification']])

    def test_scaffold_rejects_tampered_hook_before_publication(self):
        with tempfile.TemporaryDirectory() as d:
            t=Path(d);root=t/'consumer';root.mkdir()
            def git(*args):
                return subprocess.run(['git','-C',str(root),*args],check=True,
                                      capture_output=True,text=True).stdout.strip()
            git('init','-b','feature/validation-fixture')
            git('config','user.email','tests@example.invalid')
            git('config','user.name','Fixture')
            git('remote','add','origin','git@github.com:Mikeyphw/validation-fixture.git')
            (root/'README.md').write_text('before\n')
            git('add','README.md');git('commit','-m','fixture')
            payload=t/'payload';(payload/'.devtool-artifact').mkdir(parents=True)
            (payload/'README.md').write_text('after\n')
            spec,_=plan()
            (payload/'.devtool-artifact/validate.py').write_text('tampered\n')
            declared=t/'plan.json';declared.write_text(json.dumps(spec))
            dest=t/'artifact.zip'
            with self.assertRaises(adoption.AdoptionError):
                adoption.scaffold_overlay(root,payload,declared,dest,
                    expected_head=git('rev-parse','HEAD'))
            self.assertFalse(dest.exists())

    def test_native_scaffold_and_launcher_support_contract_fields(self):
        # Current-source integration, not an offline mock: no second execution engine.
        import inspect
        self.assertIn('inspect_delivery_plan',inspect.getsource(adoption.scaffold_overlay))
        self.assertIn('validation_execution_mode',inspect.getsource(launcher.apply_package))
        self.assertIn('validate-overlay',inspect.getsource(launcher.scaffold))
        self.assertIn("'delivery','session','start'",inspect.getsource(launcher.apply_package))

    def test_legacy_scaffold_mock_remains_compatible_but_new_metadata_is_strict(self):
        # Existing v3.1 ABI test uses a mocked scaffold receipt with placeholder
        # bytes. Legacy manifests have no new contract to cross-check, so the
        # existing native validate-overlay invocation remains the gate. New
        # declarations, however, must fail closed on invalid archives.
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            def fake_json(repo, devtool_root, *parts, stage):
                archive=Path(parts[parts.index('--output')+1])
                archive.write_bytes(b'legacy mocked receipt; native validator owns inspection')
                return {'source_sha':'a'*40, 'applied':False}
            with patch.object(launcher,'private_store',return_value=root), \
                 patch.object(launcher,'native_json',side_effect=fake_json), \
                 patch.object(launcher,'native') as native:
                legacy={'name':'legacy','source':{'source_sha':'a'*40},'plan':{}}
                got=launcher.scaffold(root,root,legacy,{})
                self.assertEqual(got.parent,root)
                native.assert_called_once()
            with patch.object(launcher,'private_store',return_value=root), \
                 patch.object(launcher,'native_json',side_effect=fake_json), \
                 patch.object(launcher,'native') as native:
                full={'name':'full','source':{'source_sha':'a'*40},
                      'plan':{'delivery_validation_mode':'full',
                              'validation':{'required':True,'tests':[{'id':'contract',
                              'runner':'python','command':['python3','-V']}]}}}
                with self.assertRaisesRegex(launcher.Refused,'NATIVE_SCAFFOLD_METADATA_INVALID'):
                    launcher.scaffold(root,root,full,{})
                native.assert_not_called()

    def test_tty_monitor_only_for_native_ci(self):
        import inspect
        self.assertIn('tty_live=True',inspect.getsource(launcher.observe_delivery_ci))
        self.assertIn('isatty',inspect.getsource(launcher.exec_cmd))

    def test_existing_launcher_legacy_abi_preserved(self):
        self.assertEqual(launcher.SCHEMA,'devtool.launcher-contract.v3')
        self.assertIn('validate-overlay', [x[0] for x in launcher.CLI_CONTRACT])

    def test_repo_scaffold_contract_keeps_original_api(self):
        import inspect
        self.assertIn('expected_head',inspect.signature(adoption.scaffold_overlay).parameters)


if __name__ == '__main__':unittest.main()
