"""RM23: accommodate genuine JVM output without relaxing archive safety.

No Gradle, network, signing, Android or external consumer execution.
"""
from __future__ import annotations
import struct
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile, ZipInfo, ZIP_STORED

from devtool.actions_rm12_jvm import JvmError, _archive, verify_jar
from devtool.actions_rm21_hosted_native import _jar, HostedAcceptanceError

LIMIT = 4096


def proof_class() -> bytes:
    """Minimal structurally valid JVM class with four constant-pool entries."""
    def text(value: str) -> bytes:
        blob=value.encode('ascii')
        return b'\x01'+struct.pack('>H',len(blob))+blob
    return (b'\xca\xfe\xba\xbe'+struct.pack('>HHH',0,52,5)
            +text('Proof')+b'\x07\x00\x01'
            +text('java/lang/Object')+b'\x07\x00\x03'
            +struct.pack('>HHHHHHH',0x21,2,4,0,0,0,0))


def make(path: Path, count: int, extra: list[tuple[str,bytes]] | None = None) -> None:
    with ZipFile(path,'w',compression=ZIP_STORED) as z:
        z.writestr('META-INF/MANIFEST.MF', b'Manifest-Version: 1.0\n')
        z.writestr('pkg/Proof.class',proof_class())
        for index in range(count-2):
            z.writestr(f'assets/r{index:05d}.txt',b'x')
        for name,contents in extra or []:
            z.writestr(name,contents)


class RM23Capacity(unittest.TestCase):
    def test_compiled_jar_above_previous_256_limit_is_verified(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'core-model.jar';make(path,600)
            report=verify_jar(path)
            self.assertEqual(report['kind'],'jvm-jar')
            self.assertEqual(report['classes'],1)
            self.assertEqual(_jar(path)['file'],'core-model.jar')

    def test_4096_is_bounded_and_4097_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'model.jar';make(path,LIMIT)
            with _archive(path) as z:self.assertEqual(len(z.infolist()),LIMIT)
            self.assertEqual(_jar(path)['file'],'model.jar')
            make(path,LIMIT+1)
            with self.assertRaisesRegex(JvmError,'duplicate/unsafe/encrypted'):
                verify_jar(path)
            with self.assertRaisesRegex(HostedAcceptanceError,'entries exceed bounds'):
                _jar(path)

    def test_traversal_cannot_hide_inside_increased_cap(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'model.jar';make(path,300,[('../escape.txt',b'bad')])
            with self.assertRaises(JvmError):verify_jar(path)
            with self.assertRaises(HostedAcceptanceError):_jar(path)

    def test_case_insensitive_duplicate_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'model.jar'
            make(path,300,[('ASSETS/R00000.txt',b'bad')])
            with self.assertRaises(JvmError):verify_jar(path)
            with self.assertRaises(HostedAcceptanceError):_jar(path)

    def test_symlink_member_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'model.jar';make(path,300)
            with ZipFile(path,'a') as z:
                link=ZipInfo('pkg/link.class');link.create_system=3
                link.external_attr=(0o120777<<16)
                z.writestr(link,b'pkg/Proof.class')
            with self.assertRaises(JvmError):verify_jar(path)
            with self.assertRaises(HostedAcceptanceError):_jar(path)

    def test_archive_size_budgets_unchanged(self):
        import inspect
        from devtool.actions_rm12_jvm import _checked_archive
        self.assertEqual(inspect.signature(_checked_archive).parameters['limit'].default,LIMIT)
        self.assertEqual(inspect.signature(_archive).parameters['limit'].default,LIMIT)


if __name__=='__main__':unittest.main(verbosity=2)
