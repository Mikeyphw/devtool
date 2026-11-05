"""Independent ZIP bytes and member-verification contract tests."""
from hashlib import sha256
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from devtool.actions_artifact_provenance import ArtifactProvenanceError, verify_github_zip


class ArchiveTransferTests(unittest.TestCase):
    def test_verified_bytes_and_member_sha(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'a.zip'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('receipts/verified.json', b'{"ok":true}')
            result = verify_github_zip(path, 'sha256:' + sha256(path.read_bytes()).hexdigest())
            self.assertEqual(result['members'][0]['sha256'], sha256(b'{"ok":true}').hexdigest())
            self.assertFalse(result['release_qualified'])
            with self.assertRaises(ArtifactProvenanceError):
                verify_github_zip(path, 'sha256:' + '0'*64)

    def test_traversal_symlink_and_duplicate_refused(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ('../evil', '/abs', 'file\\evil'):
                path = Path(d) / (str(len(name)) + '.zip')
                with zipfile.ZipFile(path, 'w') as z:
                    z.writestr(name, b'x')
                with self.assertRaises(ArtifactProvenanceError):
                    verify_github_zip(path, sha256(path.read_bytes()).hexdigest())
            path = Path(d) / 'link.zip'
            with zipfile.ZipFile(path, 'w') as z:
                info = zipfile.ZipInfo('link')
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(info, b'target')
            with self.assertRaises(ArtifactProvenanceError):
                verify_github_zip(path, sha256(path.read_bytes()).hexdigest())

    def test_casefold_duplicate_members_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'collision.zip'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('receipts/result.json', b'one')
                z.writestr('receipts/RESULT.json', b'two')
            with self.assertRaises(ArtifactProvenanceError):
                verify_github_zip(path, sha256(path.read_bytes()).hexdigest())

    def test_empty_and_bad_digest_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'empty.zip'
            with zipfile.ZipFile(path, 'w'):
                pass
            with self.assertRaises(ArtifactProvenanceError):
                verify_github_zip(path, sha256(path.read_bytes()).hexdigest())
            with self.assertRaises(ArtifactProvenanceError):
                verify_github_zip(path, 'x')


if __name__ == '__main__':
    unittest.main()
