from __future__ import annotations

import json
from pathlib import Path

import pytest

from devtool.dist_g3_gate import audit_source_dispositions as audit_dist_source_dispositions
from devtool.evidence_resolver import EvidenceResolver
from devtool.test_g3_gate import audit_dispositions as audit_test_dispositions
from devtool.textual_control_center import ACTION_SPECS
from devtool.ui_core.actions import ACTION_REGISTRY


def test_r04a_evidence_resolver_rejects_symbolic_unknowns_and_resolves_retained_aliases() -> None:
    root = Path(__file__).resolve().parents[1]
    resolver = EvidenceResolver(root)
    assert resolver.resolve("roadmap-test-g3-trust", execute_pytest=False).resolved is True
    assert resolver.resolve("roadmap-dist-g3-final-proof", execute_pytest=False).resolved is True
    unknown = resolver.resolve("not-a-real-r04a-evidence-alias", execute_pytest=False)
    assert unknown.resolved is False


def test_r04a_dist_and_test_g3_disposition_evidence_uses_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    assert audit_dist_source_dispositions(root)["ok"] is True
    assert audit_test_dispositions(root)["ok"] is True

    def fail_resolver(*_args, **_kwargs):
        raise RuntimeError("resolver-called")

    monkeypatch.setattr(EvidenceResolver, "require_all", fail_resolver)
    with pytest.raises(RuntimeError, match="resolver-called"):
        audit_dist_source_dispositions(root)
    with pytest.raises(RuntimeError, match="resolver-called"):
        audit_test_dispositions(root)


def test_r04a_textual_actions_are_canonical_action_registry_views() -> None:
    for action_id, spec in ACTION_SPECS.items():
        assert spec.canonical_id in ACTION_REGISTRY, action_id
        canonical = ACTION_REGISTRY[spec.canonical_id]
        assert spec.destructive is canonical.destructive
        assert spec.label == canonical.label
        assert spec.intent == canonical.intent
    assert ACTION_SPECS["cancel"].canonical_id == "operation.cancel"
    assert ACTION_SPECS["overlay.apply"].canonical_id == "overlay.apply"
    assert ACTION_SPECS["worker.recover"].canonical_id == "worker.recover"


def test_r04a_android_notifications_are_private_by_default() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud/NotificationController.java"
    text = source.read_text(encoding="utf-8")
    assert "Notification.VISIBILITY_PRIVATE" in text
    assert "Notification.VISIBILITY_PUBLIC" not in text
