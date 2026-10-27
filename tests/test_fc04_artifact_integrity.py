"""FC-04 adversarial acceptance for nested JVM and native ZIP ABI boundaries."""
from __future__ import annotations
import io
from pathlib import Path
import struct
import zipfile
import pytest
from devtool.actions_rm12_jvm import JvmError,verify_desktop_bundle,verify_jar
from devtool.actions_rm13_cpp import CppError,verify_archive


def _class() -> bytes:
    utf=lambda b:b'\x01'+struct.pack('>H',len(b))+b
    pool=utf(b'Demo')+b'\x07\x00\x01'+utf(b'java/lang/Object')+b'\x07\x00\x03'
    return b'\xca\xfe\xba\xbe'+struct.pack('>HHH',0,52,5)+pool+struct.pack('>HHHHHHH',0x21,2,4,0,0,0,0)


def _jar(*,clas:bytes|None=None,classpath:bytes=b'',signed:bool=False,nested_symlink:bool=False,
         duplicate:bool=False,giant:bool=False) -> bytes:
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('META-INF/MANIFEST.MF',b'Manifest-Version: 1.0\nMain-Class: Demo\n'+classpath+b'\n')
        z.writestr('Demo.class',_class() if clas is None else clas)
        if duplicate:z.writestr('Demo.class',_class())
        if signed:z.writestr('META-INF/DEMO.SF','Signature-Version: 1.0')
        if nested_symlink:
            x=zipfile.ZipInfo('link');x.external_attr=0o120777<<16;z.writestr(x,'../bad')
        if giant:z.writestr('blob.bin',b'x'*(16*1024*1024+1))
    return out.getvalue()


def _desktop(path:Path, nested:bytes, *, launcher:bytes|None=None, mode:int=0o100755,
             jarname:str='demo.jar', extra:bool=False) -> Path:
    expected=b'#!/bin/sh\nset -eu\nexec java -jar "$(dirname "$0")/../app/'+jarname.encode()+b'" "$@"\n'
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('app/'+jarname,nested)
        x=zipfile.ZipInfo('bin/start');x.external_attr=mode<<16
        z.writestr(x,expected if launcher is None else launcher)
        if extra:z.writestr('bin/rogue',b'#!/bin/sh\n')
    return path


def _elf(abi:str='elf-x86_64') -> bytes:
    data=bytearray(64)
    data[:4]=b'\x7fELF';data[4:6]=b'\x02\x01'
    struct.pack_into('<H',data,16,1)
    struct.pack_into('<H',data,18,62 if abi=='elf-x86_64' else 183)
    return bytes(data)


def _native(path:Path,entries:dict[str,bytes]) -> Path:
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,data in entries.items():z.writestr(name,data)
    return path


def test_fc04_real_nested_jar_and_abi_zip_positive(tmp_path):
    jar=_jar();source=tmp_path/'demo.jar';source.write_bytes(jar)
    assert verify_jar(source)['classes']==1
    bundle=_desktop(tmp_path/'desktop.zip',jar)
    receipt=verify_desktop_bundle(bundle)
    assert receipt['nested_jar_classes_verified']==1
    assert receipt['nested_jar_sha256']
    archive=_native(tmp_path/'native.zip',{'bin/demo':_elf()})
    result=verify_archive(archive,expected_abi='elf-x86_64')
    assert result['qualification']=='ABI_VERIFIED' and result['abi_verified_member_count']==1
    assert not result['release_qualified']


@pytest.mark.parametrize('invalid',[
    b'not-a-class',
    b'\xca\xfe\xba\xbe\x00\x00\x00\x34',  # old magic-only fixture
    _class()[:-1],
    _class()+b'\x00',
])
def test_fc04_nested_class_requires_structure(tmp_path,invalid):
    with pytest.raises(JvmError,match='class bytecode structure'):
        verify_desktop_bundle(_desktop(tmp_path/'bad.zip',_jar(clas=invalid)))


def test_fc04_nested_reuses_manifest_path_and_signature_refusals(tmp_path):
    variants=[(_jar(classpath=b'Class-Path: ../../secret.jar\n'),'classpath'),
              (_jar(signed=True),'signing'),
              (_jar(nested_symlink=True),'symlink'),
              (_jar(duplicate=True),'duplicate'),
              (_jar(giant=True),'budget')]
    for n,(jar,match) in enumerate(variants):
        with pytest.raises(JvmError,match=match):
            verify_desktop_bundle(_desktop(tmp_path/f'invalid-{n}.zip',jar))


@pytest.mark.parametrize('kwargs,match',[
    ({'launcher':b'#!/bin/sh\nexit 0\n'},'launcher'),
    ({'mode':0o100644},'launcher'),
    ({'extra':True},'bundle needs'),
    ({'jarname':'different.jar','launcher':b'wrong launcher'},'launcher'),
])
def test_fc04_launcher_identity_and_permissions(tmp_path,kwargs,match):
    # A valid JAR and an unreviewed launcher cannot qualify together.
    with pytest.raises(JvmError,match=match):
        verify_desktop_bundle(_desktop(tmp_path/'bad.zip',_jar(),**kwargs))


def test_fc04_zero_member_zip_and_opaque_native_names_rejected(tmp_path):
    for n,entries in enumerate([{'README.txt':b'hello'},
                                {'bin/demo':b'#!/bin/sh\n'},
                                {'bin/demo.so':b'not a native object'}]):
        with pytest.raises(CppError,match='no ABI-verified|cannot be ABI-verified'):
            verify_archive(_native(tmp_path/f'empty-{n}.zip',entries),expected_abi='elf-aarch64')


def test_fc04_wrong_arch_and_mixed_arch_refused(tmp_path):
    for n,payload in enumerate(({'bin/demo':_elf('elf-aarch64')},
                                {'bin/demo':_elf(),'lib/native.o':_elf('elf-aarch64')})):
        with pytest.raises(CppError,match='wrong ABI'):
            verify_archive(_native(tmp_path/f'wrong-{n}.zip',payload),expected_abi='elf-x86_64')


def test_fc04_pe_and_macho_architecture(tmp_path):
    pe=bytearray(160);pe[:2]=b'MZ';struct.pack_into('<I',pe,60,64);pe[64:68]=b'PE\0\0'
    struct.pack_into('<H',pe,68,0x8664)
    p=_native(tmp_path/'windows.zip',{'bin/demo.exe':bytes(pe)})
    assert verify_archive(p,expected_abi='pe-x86_64')['abi_verified_member_count']==1
    with pytest.raises(CppError,match='wrong ABI'):verify_archive(p,expected_abi='elf-x86_64')
    macho=b'\xcf\xfa\xed\xfe'+struct.pack('<I',0x01000007)+b'\x00'*32
    p=_native(tmp_path/'mac.zip',{'bin/demo':macho})
    assert verify_archive(p,expected_abi='macho-x86_64')['abi_verified_member_count']==1


def test_fc04_zip_traversal_symlink_duplicate_and_bomb_refused(tmp_path):
    variants=[('../evil',b'evil','unsafe'),
              ('bin/demo.so',b'bad','cannot be ABI-verified')]
    for n,(name,bytes_,reason) in enumerate(variants):
        p=_native(tmp_path/f'bad-{n}.zip',{'bin/demo':_elf(),name:bytes_})
        with pytest.raises(CppError,match=reason):verify_archive(p,expected_abi='elf-x86_64')
    p=tmp_path/'symlink.zip'
    with zipfile.ZipFile(p,'w') as z:
        z.writestr('bin/demo',_elf())
        info=zipfile.ZipInfo('bin/link');info.external_attr=0o120777<<16;z.writestr(info,'../target')
    with pytest.raises(CppError,match='unsafe'):verify_archive(p,expected_abi='elf-x86_64')
    p=tmp_path/'duplicate.zip'
    with zipfile.ZipFile(p,'w') as z:
        z.writestr('bin/demo',_elf());z.writestr('bin/demo',_elf())
    with pytest.raises(CppError,match='duplicate'):verify_archive(p,expected_abi='elf-x86_64')


def test_fc04_native_zip_byte_receipt_matches_payload(tmp_path):
    import hashlib,json
    native=_elf()
    receipt=json.dumps({'sha256':hashlib.sha256(native).hexdigest(),'abi':'elf-x86_64'}).encode()
    p=_native(tmp_path/'good.zip',{'bin/demo':native,'byte-receipt.json':receipt})
    assert verify_archive(p,expected_abi='elf-x86_64')['qualification']=='ABI_VERIFIED'
    receipt=json.dumps({'sha256':'0'*64,'abi':'elf-x86_64'}).encode()
    p=_native(tmp_path/'tampered.zip',{'bin/demo':native,'byte-receipt.json':receipt})
    with pytest.raises(CppError,match='byte receipt differs'):
        verify_archive(p,expected_abi='elf-x86_64')
