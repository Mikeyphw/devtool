"""Source/provenance/forward-3.x runtime compatibility acceptance.

These tests make no network calls and never alter an existing Git worktree.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from devtool.launcher_runtime_compat import (
    version_compatible, require_version, parse_version, declared_version,
    IncompatibleLauncherRuntime)
from devtool import launcher_contract_v3 as launcher

MINIMUM={'minimum':'3.1.0','major':3}


def git(root,*args):
    p=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,timeout=15)
    if p.returncode:
        raise AssertionError((args,p.stderr))
    return p.stdout.strip()


def fixture(root: Path, version: str='3.1.0') -> Path:
    root.mkdir(parents=True,exist_ok=True)
    git(root,'init','-b','feature/test-compat')
    git(root,'config','user.name','test');git(root,'config','user.email','test@example.invalid')
    git(root,'remote','add','origin','git@github.com:Mikeyphw/devtool.git')
    mod=root/'devtool/launcher_contract_v3.py';mod.parent.mkdir()
    mod.write_text("SCHEMA = 'devtool.launcher-contract.v3'\n"
                   'LAUNCHER_CONTRACT_VERSION = '+repr(version)+'\n'
                   'def main():\n    print("COMPAT_MAIN_RAN")\n    return 0\n')
    (root/'devtool/__init__.py').write_text('')
    (root/'devtool/core_cli_schema.json').write_text('{"schema_version":2,"kind":"devtool.canonical-cli-schema"}\n')
    git(root,'add','.');git(root,'commit','-m','test native runtime')
    return root


class CompatibilityTests(unittest.TestCase):
    def test_forward_in_major_3_and_same_version_allowed(self):
        for version in ('3.1.0','3.1.1','3.2.0','3.4.15','3.99.0'):
            with self.subTest(version=version):
                self.assertTrue(version_compatible(version,'3.1.0',3))
                self.assertEqual(require_version(version,MINIMUM),parse_version(version))

    def test_lower_or_other_major_rejected_even_if_higher_numerically(self):
        for version in ('2.99.99','3.0.99','4.0.0','4.1.0','10.0.0'):
            with self.subTest(version=version):
                self.assertFalse(version_compatible(version,'3.1.0',3))
                with self.assertRaisesRegex(IncompatibleLauncherRuntime,'INCOMPATIBLE'):
                    require_version(version,MINIMUM)

    def test_version_cannot_be_dynamic_malformed_or_fake(self):
        for value in ('3','3.1','3.1.0-rc1','03.1.0','3.01.0','4.x','',None,[],123):
            with self.subTest(value=value):
                with self.assertRaises(IncompatibleLauncherRuntime):parse_version(value)
        with self.assertRaises(IncompatibleLauncherRuntime):require_version('3.2.0',{'minimum':'3.1.0','major':4})
        with self.assertRaises(IncompatibleLauncherRuntime):require_version('3.2.0',{'minimum':'3.1.0','major':3,'extra':True})

    def test_exact_literal_marker_only(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'launcher.py'
            for text in ('LAUNCHER_CONTRACT_VERSION = "3.1.0"',
                         'LAUNCHER_CONTRACT_VERSION = "3.9.0"'):
                p.write_text(text);self.assertEqual(declared_version(p),text.split('"')[1])
            for text in ('LAUNCHER_CONTRACT_VERSION = f"3.1.{0}"',
                         'LAUNCHER_CONTRACT_VERSION = "3.1.0"\nLAUNCHER_CONTRACT_VERSION = "4.0.0"',
                         'LAUNCHER_CONTRACT_VERSION = "4.0.0"'):
                p.write_text(text)
                if text.endswith('"4.0.0"') and '\n' not in text:
                    self.assertEqual(declared_version(p),'4.0.0')
                else:
                    with self.assertRaises(IncompatibleLauncherRuntime):declared_version(p)

    def test_actual_runtime_origin_git_files_commit_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            p=fixture(Path(temp)/'repo','3.9.3')
            bound=launcher.check_compatible_runtime(p,{'runtime_compatibility':MINIMUM})
            self.assertEqual(bound['sha'],git(p,'rev-parse','HEAD'))
            self.assertEqual(bound['tree'],git(p,'rev-parse','HEAD^{tree}'))
            self.assertEqual(bound['module'],git(p,'rev-parse','HEAD:devtool/launcher_contract_v3.py'))
            self.assertEqual(bound['version'],'3.9.3')
            self.assertEqual(bound['schema'],git(p,'rev-parse','HEAD:devtool/core_cli_schema.json'))

    def test_runtime_dirty_or_wrong_origin_refuses(self):
        with tempfile.TemporaryDirectory() as temp:
            p=fixture(Path(temp)/'repo')
            (p/'devtool/launcher_contract_v3.py').write_text('LAUNCHER_CONTRACT_VERSION="3.9.0"\n')
            with self.assertRaises(launcher.Refused):launcher.check_compatible_runtime(p,{'runtime_compatibility':MINIMUM})
            git(p,'restore','devtool/launcher_contract_v3.py')
            git(p,'remote','set-url','origin','git@github.com:Other/devtool.git')
            with self.assertRaises(launcher.Refused):launcher.check_compatible_runtime(p,{'runtime_compatibility':MINIMUM})

    def test_exact_runtime_current_git_tree_not_build_pin(self):
        with tempfile.TemporaryDirectory() as temp:
            p=fixture(Path(temp)/'repo','3.3.0')
            b=launcher.check_compatible_runtime(p,{
                'source_sha':'a'*40,'source_tree':'b'*40,'module_blob':'c'*40,
                'schema_blob':'d'*40,'runtime_compatibility':MINIMUM})
            self.assertNotEqual(b['sha'],'a'*40)
            self.assertNotEqual(b['module'],'c'*40)

    def test_legacy_packages_are_exact_pinned(self):
        code=launcher.bootstrap_content({'source_sha':'a'*40,'source_tree':'b'*40,'module_blob':'c'*40})
        self.assertIn('PINS',code)
        self.assertIn("git(path,'rev-parse','HEAD')==PINS['source_sha']",code)
        self.assertNotIn('LAUNCHER_V3_RUNTIME_VERSION_PASS',code)

    def test_new_package_records_immutable_build_runtime_provenance(self):
        ident={'repository':'Mikeyphw/devtool','branch':'feature/test-compat',
               'source_sha':'a'*40,'source_tree':'b'*40}
        runtime={'repository':'Mikeyphw/devtool','source_sha':'c'*40,
                 'source_tree':'d'*40,'module_blob':'e'*40,'launcher_version':'3.1.0'}
        changes=[{'path':'devtool/new.py','action':'add','source_blob_sha':'',
                  'payload_sha256':'f'*64}]
        m=launcher.make_manifest('test-compat',ident,runtime,changes,'test commit','title','body','main','1'*40)
        launcher.verify_manifest(m)
        self.assertEqual(m['devtool']['source_sha'],'c'*40)
        self.assertEqual(m['devtool']['runtime_compatibility'],MINIMUM)
        bad=json.loads(json.dumps(m))
        bad['devtool']['runtime_compatibility']={'minimum':'3.1.0','major':4}
        with self.assertRaises(launcher.Refused):launcher.verify_manifest(bad)

    def test_new_bootstrap_accepts_greater_3x_deny_4x_without_import(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            source=fixture(base/'repo','3.2.1')
            manifest={'devtool':{'source_sha':'a'*40,'runtime_compatibility':MINIMUM},
                      'source':{'repository':'Mikeyphw/devtool','branch':'feature/test-compat'},
                      'schema':'devtool.launcher-contract.v3','name':'test','inventory':[],
                      'limitations':{'release_qualified':False}}
            zipapp=base/'test.pyz'
            boot=launcher.compatible_bootstrap_content({'launcher_version':'3.1.0'})
            with zipfile.ZipFile(zipapp,'w') as z:
                z.writestr('__main__.py',boot)
                z.writestr('manifest.json',json.dumps(manifest))
            run=lambda:subprocess.run([sys.executable,'-B',str(zipapp),'check',
                                      '--repo',str(source),'--devtool-repo',str(source)],
                                     text=True,capture_output=True,timeout=15,env={**os.environ,'NO_COLOR':'1'})
            one=run();self.assertEqual(one.returncode,0,(one.stdout,one.stderr))
            self.assertIn('version=3.2.1',one.stdout)
            self.assertIn('COMPAT_MAIN_RAN',one.stdout)
            self.assertEqual(git(source,'rev-parse','HEAD'),one.stdout.split('head=')[1][:40])
            (source/'devtool/launcher_contract_v3.py').write_text("SCHEMA = 'devtool.launcher-contract.v3'\n"
                   'LAUNCHER_CONTRACT_VERSION = "4.0.0"\n'
                   'def main():\n    print("UNSAFE_MAIN")\n    return 0\n')
            git(source,'add','.');git(source,'commit','-m','break ABI to 4')
            two=run();self.assertNotEqual(two.returncode,0)
            self.assertIn('not verified compatible 3.x',two.stderr)
            self.assertNotIn('UNSAFE_MAIN',two.stdout)


    def test_versioned_package_roundtrip_keeps_provenance(self):
        ident={'repository':'Mikeyphw/devtool','branch':'feature/test-compat',
               'source_sha':'a'*40,'source_tree':'b'*40}
        runtime={'repository':'Mikeyphw/devtool','source_sha':'c'*40,
                 'source_tree':'d'*40,'module_blob':'e'*40,'launcher_version':'3.1.0'}
        item={'path':'devtool/new.py','action':'add','source_blob_sha':'',
              'payload_sha256':hashlib.sha256(b'original bytes\n').hexdigest()}
        m=launcher.make_manifest('versioned-test',ident,runtime,[item],
                                 'commit','title','body','main','1'*40)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'immutable.pyz'
            blob=launcher.zip_bytes({
                '__main__.py':launcher.bootstrap_content(runtime).encode(),
                'manifest.json':(json.dumps(m,sort_keys=True)+'\n').encode(),
                'payload/devtool/new.py':b'original bytes\n'})
            path.write_bytes(blob)
            stored,payload=launcher.read_embedded_package(path)
            self.assertEqual(stored,m)
            self.assertEqual(payload['payload/devtool/new.py'],b'original bytes\n')
            self.assertEqual(stored['devtool']['source_sha'],'c'*40)

    def test_verification_rejects_v4_abi_even_with_forged_3x_version(self):
        with tempfile.TemporaryDirectory() as temp:
            p=fixture(Path(temp)/'repo','3.2.0')
            path=p/'devtool/launcher_contract_v3.py'
            text=path.read_text().replace('devtool.launcher-contract.v3','devtool.launcher-contract.v4')
            path.write_text(text)
            git(p,'add','.');git(p,'commit','-m','incompatible schema')
            with self.assertRaisesRegex(launcher.Refused,'SCHEMA_MISMATCH'):
                launcher.check_compatible_runtime(p,{'runtime_compatibility':MINIMUM})

    def test_freeze_runtime_is_detached_without_rewriting_feature_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            p=fixture(Path(temp)/'repo','3.2.0')
            before=git(p,'rev-parse','HEAD')
            cache=Path(temp)/'state'/'devtool'/'launcher-runtimes'
            cache.mkdir(parents=True)
            with patch.object(launcher,'_runtime_cache_root',return_value=cache):
                dest=launcher.resolve_compatible_runtime(p,p,{'runtime_compatibility':MINIMUM})
                self.assertEqual(git(dest,'rev-parse','HEAD'),before)
                self.assertEqual(git(p,'rev-parse','HEAD'),before)
                self.assertEqual(git(p,'branch','--show-current'),'feature/test-compat')
                self.assertEqual(git(dest,'branch','--show-current'),'')
                reused=launcher.resolve_compatible_runtime(p,p,{'runtime_compatibility':MINIMUM})
                self.assertEqual(dest,reused)

    def test_native_cli_contract_matches_canonical_schema(self):
        # This is a real checked-in schema projection, not a synthetic stub.
        from devtool.core_parser import parser_identity_projection
        available=parser_identity_projection()['commands']
        expected=set(launcher.CLI_CONTRACT)
        self.assertIn(('validate-overlay',), expected)
        self.assertNotIn(('validate','overlay'), expected)
        self.assertTrue(expected <= available,
                        'Native launcher contract drift: '+repr(sorted(expected-available)))

    def test_scaffold_invokes_flat_native_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            archive_calls=[]
            def fake_json(repo,devtool_root,*parts,stage):
                self.assertEqual(parts[:3],('delivery','adoption','scaffold'))
                output=Path(parts[parts.index('--output')+1]);output.write_bytes(b'validated by mock')
                return {'source_sha':'a'*40,'applied':False}
            def fake_native(repo,devtool_root,*parts,stage,timeout,live):
                archive_calls.append((parts,stage,timeout,live))
            with patch.object(launcher,'private_store',return_value=root),                  patch.object(launcher,'native_json',side_effect=fake_json),                  patch.object(launcher,'native',side_effect=fake_native):
                result=launcher.scaffold(root,root,{'name':'abi-fix','source':{'source_sha':'a'*40},'plan':{}}, {})
            self.assertEqual(result.parent,root)
            self.assertEqual(len(archive_calls),1)
            parts,stage,timeout,live=archive_calls[0]
            self.assertEqual(parts[:2],('validate-overlay',str(result)))
            self.assertEqual(stage,'native-validate-overlay')
            self.assertTrue(live)

    def test_native_probe_rejects_removed_cli_command_even_when_version_matches(self):
        from types import SimpleNamespace
        import sys
        with tempfile.TemporaryDirectory() as temp:
            p=fixture(Path(temp)/'repo','3.2.0')
            manifest={'devtool':{'runtime_compatibility':MINIMUM,
                                  'source_sha':'a'*40,'source_tree':'b'*40,
                                  'module_blob':'c'*40,'schema_blob':'d'*40}}
            stub=SimpleNamespace(parser_identity_projection=lambda:{'commands':set()})
            with patch.dict(sys.modules,{'devtool.core_parser':stub}):
                with self.assertRaisesRegex(launcher.Refused,'MISSING_NATIVE_CLI'):
                    launcher.runtime_probe(p,p,manifest)


if __name__=='__main__':unittest.main()
