"""ACTIONS-A06: installed-wheel contract, not a source-only import smoke test.

No network or repository mutation: build using the current host's packaging
backend from a disposable source copy and install into an isolated target.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'core_cli_schema.json'


def _run(cmd: list[str], cwd: Path, timeout: int = 110) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PIP_NO_INDEX'] = '1'
    env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                            text=True, timeout=timeout)
    assert result.returncode == 0, '\n'.join((str(cmd),result.stdout[-2000:],result.stderr[-2000:]))
    return result


def test_a06_package_manifest_explicitly_owns_canonical_schema():
    cfg = tomllib.loads((ROOT/'pyproject.toml').read_text('utf-8'))
    items = cfg['tool']['setuptools']['package-data']['devtool']
    assert items.count(SCHEMA) == 1
    assert cfg['tool']['setuptools']['include-package-data'] is True
    assert cfg['build-system']['build-backend'] == 'setuptools.build_meta'


def test_a06_schema_is_the_same_runtime_authority():
    import json
    src = (ROOT/'devtool'/SCHEMA)
    assert src.is_file() and not src.is_symlink()
    schema = json.loads(src.read_text('utf-8'))
    assert schema['kind'] == 'devtool.canonical-cli-schema'
    assert schema['schema_version'] == 2
    parser_source = (ROOT/'devtool/core_parser.py').read_text('utf-8')
    assert 'with_name("core_cli_schema.json")' in parser_source


def test_a06_real_wheel_build_and_isolated_installed_import():
    with tempfile.TemporaryDirectory(prefix='devtool-a06-wheel-') as folder:
        root = Path(folder)
        source = root/'src'
        source.mkdir()
        for rel in ('pyproject.toml','README.md'):
            shutil.copy2(ROOT/rel,source/rel)
        shutil.copytree(ROOT/'devtool',source/'devtool',
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc','.pytest_cache'))
        wheels = root/'wheels'
        wheels.mkdir()
        _run([sys.executable,'-m','pip','wheel','--no-deps',
              '--no-build-isolation','--no-index','-w',str(wheels),str(source)],root)
        produced = sorted(wheels.glob('devtool-*.whl'))
        assert len(produced) == 1, 'Exactly one Devtool wheel must be created'
        wheel = produced[0]
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            assert names.count('devtool/core_cli_schema.json') == 1
            original = (ROOT/'devtool'/SCHEMA).read_bytes()
            assert hashlib.sha256(archive.read('devtool/core_cli_schema.json')).digest() == hashlib.sha256(original).digest()
        dest = root/'site'
        _run([sys.executable,'-m','pip','install','--no-deps','--no-index',
              '--no-compile','--target',str(dest),str(wheel)],root)
        # -S eliminates ambient site-packages; the import must come from the
        # installed isolated wheel, not the source checkout or a PYTHONPATH.
        code = ("import sys;from pathlib import Path;"
                "sys.path.insert(0,sys.argv[1]);"
                "import devtool;from devtool.core_parser import canonical_cli_schema;"
                "assert Path(devtool.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve());"
                "assert canonical_cli_schema()['kind']=='devtool.canonical-cli-schema'")
        _run([sys.executable,'-S','-c',code,str(dest)],root)
