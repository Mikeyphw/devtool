"""RMP-H1 C05/C06 cross-consumer fail-closed negative controls."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from devtool.environment_composition import ProviderExecutableIdentity, _direnv_probe
from devtool.presentation_surfaces import compose_dashboard, status_from_exo


@pytest.mark.parametrize("bad_status", ["blocked", "cancelled", "unknown", "skipped", "interrupted"])
def test_dashboard_cannot_turn_non_success_into_passed(bad_status: str) -> None:
    healthy = status_from_exo([{"id": "ok", "status": "passed"}])
    untrusted = status_from_exo([{"id": "bad", "status": bad_status}])
    composite = compose_dashboard(healthy, untrusted, limit=1)
    assert composite.truncated
    assert composite.status != "passed"
    assert composite.status != "reused"


def test_dashboard_failure_after_visible_window_preserves_failed() -> None:
    events = status_from_exo([{"id": f"ok{i}", "status": "passed"} for i in range(5)])
    fail = status_from_exo([{"id": "bad", "status": "failed"}])
    assert compose_dashboard(events, fail, limit=1).status == "failed"


def test_dashboard_normal_success_and_active_run() -> None:
    healthy = status_from_exo([{"id": "ok", "status": "passed"}])
    assert compose_dashboard(healthy, healthy).status == "passed"
    active = status_from_exo([{"id": "active", "status": "running"}])
    assert compose_dashboard(healthy, active).status == "running"


def test_direnv_identity_excludes_secret_values_but_tracks_nonsecret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from devtool import environment_composition as envcomp

    (tmp_path / ".envrc").write_text("export DEMO=ok\n")
    secrets = {"token": "c06-token-A", "password": "c06-password-A", "ordinary": "demo-A", "nested": "nested-A"}

    def fake_readonly(argv: list[str], **_: object) -> str:
        if argv[1:] == ["version"]:
            return "2.0"
        if argv[1:] == ["status", "--json"]:
            return json.dumps({"state": {"foundRC": {"status": {"text": "ALLOWED"}}},
                               "config": {"provider_options": {"api_token": secrets["token"], "options": {"password": secrets["password"], "theme": secrets["nested"]}}}})
        if argv[1:] == ["export", "json"]:
            return json.dumps({"API_TOKEN": secrets["token"], "DATABASE_PASSWORD": secrets["password"], "DEMO": secrets["ordinary"]})
        return ""

    monkeypatch.setattr(envcomp, "_run_readonly", fake_readonly)
    identity = ProviderExecutableIdentity(name="direnv", path="/fake/direnv")

    def probe() -> tuple[str, str, bool]:
        values = _direnv_probe(tmp_path, identity, {}, requested=True)
        assert values[0] is True
        return values[7], values[4], values[8]

    initial = probe()
    assert initial[2]
    secrets["token"] = "c06-token-B"
    secrets["password"] = "c06-password-B"
    assert probe() == initial, "credential rotation must not perturb exported/status identities"
    secrets["ordinary"] = "demo-B"
    after_export_change = probe()
    assert after_export_change[0] != initial[0], "nonsecret export changes remain identity-relevant"
    secrets["nested"] = "nested-B"
    assert probe()[1] != after_export_change[1], "nonsecret status config changes remain identity-relevant"
