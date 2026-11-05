"""RM21 admission and archive gates; tests run without Node/Gradle execution."""
from __future__ import annotations
import importlib.util
import tempfile
import unittest
from pathlib import Path
import subprocess
import zipfile

from devtool.actions_rm21_hosted_native import (
    HostedAcceptanceError, _checkout, _ensure_source_unchanged, _external_source,
    _jar, _outputs, _project, _child_env,
)

PREPARE = Path(__file__).resolve().parent / 'fixtures/rm21/prepare.py'
spec = importlib.util.spec_from_file_location('rm21_prepare', PREPARE)
assert spec and spec.loader
prepared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepared)


class HostedAdmission(unittest.TestCase):
    def repo(self, tmp):
        root = Path(tmp) / 'external-fixture'
        sha = prepared.prepare(root)
        return root, sha

    def test_source_bound_git_sha_and_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, sha = self.repo(tmp)
            identity = _checkout(root, sha)
            self.assertEqual(identity['consumer_commit'], sha)
            _ensure_source_unchanged(root, sha, identity['consumer_tree'])
            with self.assertRaisesRegex(HostedAcceptanceError, 'expected commit'):
                _checkout(root, '0' * 40)
            with self.assertRaisesRegex(HostedAcceptanceError, 'full hex'):
                _checkout(root, 'main')

    def test_tracked_source_drift_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, sha = self.repo(tmp)
            identity = _checkout(root, sha)
            (root/'web/scripts/build.mjs').write_text('console.log("tampered");')
            with self.assertRaisesRegex(HostedAcceptanceError, 'tracked consumer bytes'):
                _ensure_source_unchanged(root, sha, identity['consumer_tree'])
            with self.assertRaisesRegex(HostedAcceptanceError, 'dirty'):
                _checkout(root, sha)

    def test_unsafe_project_and_symlink_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.repo(tmp)
            self.assertEqual(_project(root, 'web'), root/'web')
            for bad in ('../web', 'web/../web', '/web', 'web//src', 'web\\src'):
                with self.subTest(path=bad), self.assertRaises(HostedAcceptanceError):
                    _project(root, bad)
            (root/'link').symlink_to(root/'web', target_is_directory=True)
            with self.assertRaisesRegex(HostedAcceptanceError, 'linked'):
                _project(root, 'link')

    def test_output_set_requires_manifest_regular_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'dist';folder.mkdir()
            (folder/'app.js').write_bytes(b'app')
            with self.assertRaises(HostedAcceptanceError):
                _outputs(folder)
            (folder/'manifest.json').write_text('{"files": {}}')
            self.assertEqual(_outputs(folder), ['dist/app.js','dist/manifest.json'])
            (folder/'shadow').symlink_to(folder/'app.js')
            with self.assertRaisesRegex(HostedAcceptanceError, 'symlink'):
                _outputs(folder)

    def test_external_consumer_requires_distinct_exact_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, sha = self.repo(tmp)
            self.assertFalse(_external_source(root, '')['external_consumer_verified'])
            with self.assertRaises(HostedAcceptanceError):
                _external_source(root, 'Mikeyphw/devtool')
            with self.assertRaises(HostedAcceptanceError):
                _external_source(root, 'owner/repo;exit 0')
            with self.assertRaises(HostedAcceptanceError):
                _external_source(root, 'owner/consumer')
            subprocess.run(['git','-C',str(root),'remote','add','origin',
                            'https://github.com/owner/consumer.git'],check=True)
            self.assertTrue(_external_source(root,'owner/consumer')['external_consumer_verified'])
            _checkout(root,sha)

    def test_nonsecret_environment_projection(self):
        import os
        prior = os.environ.get('GITHUB_TOKEN')
        os.environ['GITHUB_TOKEN']='sensitive-fake-value'
        try:
            child=_child_env()
            self.assertNotIn('GITHUB_TOKEN',child)
            self.assertNotIn('PYTHONPATH',child)
            self.assertEqual(child['npm_config_ignore_scripts'],'true')
        finally:
            if prior is None:os.environ.pop('GITHUB_TOKEN',None)
            else:os.environ['GITHUB_TOKEN']=prior

    def test_verified_jar_rejects_traversal_and_missing_classes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'output.jar'
            def make(members):
                with zipfile.ZipFile(path,'w') as z:
                    for name,data in members:
                        z.writestr(name,data)
            make([('META-INF/MANIFEST.MF','Manifest-Version: 1.0\n'),
                  ('devtool/rm21/Proof.class',b'\xca\xfe\xba\xbe')])
            result=_jar(path)
            self.assertEqual(len(result['sha256']),64)
            make([('META-INF/MANIFEST.MF','Manifest-Version: 1.0\n'),
                  ('../../evil.class',b'bad')])
            with self.assertRaisesRegex(HostedAcceptanceError,'unsafe'):
                _jar(path)
            make([('META-INF/MANIFEST.MF','Manifest-Version: 1.0\n'),
                  ('data.txt',b'not a class')])
            with self.assertRaisesRegex(HostedAcceptanceError,'compiled'):
                _jar(path)


if __name__=='__main__':unittest.main()
