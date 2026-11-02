"""R39-R4 hosted regression repairs (non-release source contracts)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from devtool import actions_android_lab_export as r34

ROOT = Path(__file__).resolve().parents[1]


def test_r34_generator_emits_reviewed_node24_actions_exactly():
    reviewed = {
        'CHECKOUT': ('checkout', 'd23441a48e516b6c34aea4fa41551a30e30af803'),
        'PYTHON': ('setup-python', 'ece7cb06caefa5fff74198d8649806c4678c61a1'),
        'JAVA': ('setup-java', 'b6effb05e454b25005698d916606bdc6ffcbf961'),
        'GRADLE': ('setup-gradle', '0723195856401067f7a2779048b490ace7a47d7c'),
        'UPLOAD': ('upload-artifact', 'cf430e030ddbb5b0abf93d22962f4752f3646cd9'),
        'DOWNLOAD': ('download-artifact', '9000827ccba6bdab643e8b6fd33ac0654aef8333'),
    }
    generated = r34.generate(ROOT)
    assert generated == (ROOT / r34.WORKFLOW).read_text()
    for constant, (action, digest) in reviewed.items():
        assert getattr(r34, constant) == digest
        assert f'{action}@{digest}' in generated
    assert 'release_qualified: true' not in generated


def test_historical_g00_bytes_are_separate_from_current_workflows():
    index = json.loads((ROOT/'docs/actions/ACTIONS_G00_G01_EVIDENCE_INDEX.json').read_text())
    assert index['snapshot_source']['head'] == 'e72335640aab0db58ae96c3e319a6bcec1e2ac19'
    archive = ROOT/'docs/actions/frozen_source/e7233564'
    for relative, old_sha in index['snapshot_source']['workflow_sha256'].items():
        assert hashlib.sha256((archive/relative).read_bytes()).hexdigest() == old_sha
        current = ROOT/relative
        assert current.exists()
        assert current.read_bytes() != (archive/relative).read_bytes()


def test_r34_helper_import_is_local_and_unshadowable(tmp_path):
    """A third-party regular `tools` package must not hijack R34 collection."""
    import os
    import subprocess
    import sys

    shadow = tmp_path / 'shadow' / 'tools'
    shadow.mkdir(parents=True)
    (shadow / '__init__.py').write_text('MARKER = "unrelated-installed-package"\n')
    script = r'''
import importlib.util
from pathlib import Path
import tools
assert tools.MARKER == "unrelated-installed-package"
root = Path.cwd()
p = root / "tests/test_r34_generated_android_lab.py"
spec = importlib.util.spec_from_file_location("r34_shadow_test", p)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.EVIDENCE_FILE.resolve() == (root/"tools/r33_hosted_android_evidence.py").resolve()
assert Path(module.graph_plan.__code__.co_filename).resolve() == module.EVIDENCE_FILE.resolve()
from devtool import actions_android_lab_export as r34
assert Path(r34._source_evidence_module().__file__).resolve() == module.EVIDENCE_FILE.resolve()
assert r34.generate(root) == (root/r34.WORKFLOW).read_text()
print("R39_R4_R34_TERMUX_TOOLS_SHADOW_PASS")
'''
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join((str(shadow.parent), str(ROOT)))
    result = subprocess.run([sys.executable, '-c', script], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'R39_R4_R34_TERMUX_TOOLS_SHADOW_PASS' in result.stdout
