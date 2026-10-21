"""NEXT-00..06 roadmap audit: real missing provider-contract falsification gates."""
from __future__ import annotations
import base64
import csv
import hashlib
import io
import subprocess
import zipfile
from pathlib import Path

import pytest
from devtool.actions_distributions import DistributionError, verify_go_binary, verify_wheel


def _wheel(path: Path, *, wheel_tag='py3-none-any', script_target='demo:main') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    info='demo_pkg-1.0.dist-info'
    members={
        f'{info}/METADATA':b'Metadata-Version: 2.1\nName: demo-pkg\nVersion: 1.0\n',
        f'{info}/WHEEL':f'Wheel-Version: 1.0\nTag: {wheel_tag}\n'.encode(),
        f'{info}/entry_points.txt':f'[console_scripts]\ndemo = {script_target}\n'.encode(),
        'demo.py':b'def main(): return 0\n',
    }
    lines=io.StringIO(); writer=csv.writer(lines,lineterminator='\n')
    for name, data in members.items():
        digest=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow((name,'sha256='+digest,str(len(data))))
    writer.writerow((f'{info}/RECORD','',''))
    members[f'{info}/RECORD']=lines.getvalue().encode()
    with zipfile.ZipFile(path,'w') as z:
        for name,data in members.items():z.writestr(name,data)


def test_reject_wheel_filename_and_metadata_tag_conflict(tmp_path):
    wheel=tmp_path/'demo_pkg-1.0-py3-none-any.whl'
    _wheel(wheel,wheel_tag='cp311-cp311-manylinux2014_x86_64')
    with pytest.raises(DistributionError,match='tags conflict'):
        verify_wheel(wheel,name='demo-pkg',version='1.0',entrypoints=['demo'])
    _wheel(wheel)
    assert verify_wheel(wheel,name='demo-pkg',version='1.0',entrypoints=['demo'])['record_sha256_verified']


def test_reject_wheel_console_script_target_drift(tmp_path):
    wheel=tmp_path/'demo_pkg-1.0-py3-none-any.whl'
    _wheel(wheel,script_target='evil:run')
    with pytest.raises(DistributionError,match='script target'):
        verify_wheel(wheel,name='demo-pkg',version='1.0',entrypoints=['demo'],
                     entrypoint_targets={'demo':'demo:main'})
    _wheel(wheel)
    assert verify_wheel(wheel,name='demo-pkg',version='1.0',entrypoints=['demo'],
                        entrypoint_targets={'demo':'demo:main'})['entrypoints_verified']==['demo']


def test_reject_go_binary_cgo_mismatch_even_if_elf_and_module_match(tmp_path):
    (tmp_path/'go.mod').write_text('module example.org/audit\n\ngo 1.23\n')
    (tmp_path/'main.go').write_text('package main\nfunc main(){}\n')
    binary=tmp_path/'app'
    import os
    env={**os.environ,'CGO_ENABLED':'1','GOTOOLCHAIN':'local'}
    subprocess.run(['go','build','-o',str(binary),'.'],cwd=tmp_path,env=env,
                   check=True,capture_output=True,timeout=100)
    with pytest.raises(DistributionError,match='buildinfo GOOS/GOARCH/CGO'):
        verify_go_binary(binary,goos='linux',goarch='amd64',cgo=False,module='example.org/audit')
    assert verify_go_binary(binary,goos='linux',goarch='amd64',cgo=True,
                            module='example.org/audit')['go_buildinfo']['cgo_matches']
