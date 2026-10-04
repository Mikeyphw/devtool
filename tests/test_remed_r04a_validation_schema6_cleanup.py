import json
import tempfile
from pathlib import Path

from devtool.cleanup_lifecycle import build_cleanup_ownership_view
from devtool.validation_sessions import active_session_marker, load_session, session_path


def test_schema6_validation_session_migrates_and_cleanup_authority_reads_it() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".git").mkdir()
        session_id = "schema6-active"
        state = session_path(root, session_id)
        state.parent.mkdir(parents=True)
        payload = {
            "schema_version": 6,
            "session_id": session_id,
            "repo_root": str(root),
            "project_root": str(root),
            "project_type": "python",
            "target": "devtool",
            "variant": "",
            "status": "running",
            "created_at": "2026-10-03T00:00:00Z",
            "updated_at": "2026-10-03T00:00:00Z",
            "fingerprints": {},
            "revision": 1,
            "phases": [],
            "apply_context": {},
            "options": {},
        }
        state.write_text(json.dumps(payload), encoding="utf-8")
        marker = active_session_marker(root)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"schema_version": 6, "session_id": session_id, "status": "running"}),
            encoding="utf-8",
        )

        session = load_session(root, session_id)
        assert session.status == "running"
        assert session.revision == 1

        view = build_cleanup_ownership_view(root, strict_authorities=True)
        assert any(ref.source == "validation" and session_id in ref.reference_id for ref in view.references)
        assert any(session_id in blocker for blocker in view.global_blockers)
