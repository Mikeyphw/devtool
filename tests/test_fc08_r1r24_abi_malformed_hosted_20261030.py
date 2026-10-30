"""R1R24: refuse malformed copies of an independently built Linux CMake ELF."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('fc08_r1r24_runner', ROOT/'tools/fc08_host_workloads.py')
assert SPEC and SPEC.loader
h=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)

@pytest.fixture
def real_compiled(tmp_path):
    if sys.platform!='linux' or not shutil.which('c++'):
        pytest.skip('requires genuine Linux C++ toolchain')
    project=tmp_path/'cmake'
    project.mkdir()
    source=project/'fc08main.cpp'
    source.write_text('int main(){return 0;}\n')
    proc=subprocess.run(['c++',str(source),'-o',str(project/'fc08main')],
                        capture_output=True,text=True,timeout=35)
    assert proc.returncode==0,proc.stderr[-400:]
    return project

def test_real_elf_three_refusals_and_immutable_source(real_compiled):
    binary=real_compiled/'fc08main'
    before=binary.read_bytes()
    receipt=h.linux_native_abi_malformed_refusal(real_compiled)
    assert receipt['rejected_cases']==['ELF-class','unknown-machine','symlink']
    assert receipt['original_binary_unchanged'] is True
    assert receipt['release_qualified'] is False
    assert receipt['original_elf_sha256']==h.sha_bytes(before)
    assert all(len(x)==64 for x in receipt['malformed_sha256'].values())
    assert binary.read_bytes()==before
    assert list(real_compiled.rglob('fc08-abi-refusal-*'))==[]

def test_false_acceptance_rejected(real_compiled,monkeypatch):
    import devtool.actions_rm13_cpp as rm13
    original=(real_compiled/'fc08main').read_bytes()
    monkeypatch.setattr(rm13,'verify_binary',lambda *a,**kw: {'abi':'elf-x86_64'})
    with pytest.raises(RuntimeError,match='FC08_LINUX_MALFORMED_ABI_ACCEPTED:ELF-class'):
        h.linux_native_abi_malformed_refusal(real_compiled)
    assert (real_compiled/'fc08main').read_bytes()==original

def test_wrong_reason_rejected(real_compiled,monkeypatch):
    import devtool.actions_rm13_cpp as rm13
    original=(real_compiled/'fc08main').read_bytes()
    def unrelated(*a,**kw):
        raise rm13.CppError('RM-13 network failed unrelated to ELF')
    monkeypatch.setattr(rm13,'verify_binary',unrelated)
    with pytest.raises(RuntimeError,match='FC08_LINUX_MALFORMED_ABI_WRONG_REFUSAL_REASON:ELF-class'):
        h.linux_native_abi_malformed_refusal(real_compiled)
    assert (real_compiled/'fc08main').read_bytes()==original

def test_missing_and_ambiguous_binary_refused(real_compiled):
    binary=real_compiled/'fc08main'
    binary.unlink()
    with pytest.raises(RuntimeError,match='BINARY_AMBIGUOUS'):
        h.linux_native_abi_malformed_refusal(real_compiled)
    (real_compiled/'fc08main').write_bytes(b'not an ELF')
    with pytest.raises(RuntimeError,match='NOT_REAL_X86_64_ELF'):
        h.linux_native_abi_malformed_refusal(real_compiled)

def test_receipt_contract_requires_new_real_check():
    assert h.EXPECTED_CHECKS['linux-polyglot'].count('linux-native-abi-malformed-refusal')==1
    assert len(h.EXPECTED_CHECKS['linux-polyglot'])==15
    assert len(h.EXPECTED_CHECKS['github-actions-dag'])==5
    assert len(h.EXPECTED_CHECKS['windows-dotnet-msvc'])==8
    assert len(h.EXPECTED_CHECKS['macos-native'])==3
