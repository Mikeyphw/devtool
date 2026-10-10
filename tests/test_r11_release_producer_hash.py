from __future__ import annotations
import hashlib
from pathlib import Path
from devtool.android.release import AndroidReleaseOrchestrator
from devtool.models import ProjectConfig

def test_release_artifacts_require_producer_sha(tmp_path):
    apk = tmp_path / "app-release.apk"; apk.write_bytes(b"release")
    cfg = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="android", runner="android", variant="release")
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    def select(**fields):
        item = {"valid": True, "type": "apk", "variant": "release", "path": str(apk), **fields}
        return AndroidReleaseOrchestrator._release_artifacts(cfg, {"items": [item]})
    assert not select()
    assert not select(sha256="")
    assert not select(sha256="0" * 64)
    assert not select(sha256="bad hash")
    assert not select(sha256=digest, current_sha256="0" * 64)
    assert not select(sha256=digest, producer_sha256="0" * 64)
    assert select(sha256=digest)[0][1]["sha256"] == digest
    assert select(current_sha256=digest)[0][1]["sha256"] == digest
    assert select(sha256=digest, current_sha256=digest, producer_sha256=digest)
