from __future__ import annotations

import json
import pytest
from devtool.models import CommandSpec
from devtool.validation_sessions import serialize_spec, ValidationSessionError, deserialize_spec


def test_sensitive_generic_env_never_persisted(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "V93_LONG_SECRET_VALUE")
    spec=CommandSpec("publish", ["publish", "--token", "@env:API_TOKEN"], env={"API_TOKEN":"V93_LONG_SECRET_VALUE","SAFE":"1"})
    result=serialize_spec(spec)
    assert result["env"] == {"SAFE":"1"}
    assert "V93_LONG_SECRET_VALUE" not in json.dumps(result)
    assert "@env:API_TOKEN" in deserialize_spec(result).argv


@pytest.mark.parametrize("argv", [
    ["publish", "--token", "V93_LONG_SECRET_VALUE"],
    ["publish", "--token=V93_LONG_SECRET_VALUE"],
    ["publish", "--password", "unsafe-text-value"],
    ["publish", "--api-key=unapproved-key"],
    ["publish", "V93_LONG_SECRET_VALUE"],
])
def test_inline_secrets_fail_closed(monkeypatch, argv):
    monkeypatch.setenv("API_TOKEN", "V93_LONG_SECRET_VALUE")
    with pytest.raises(ValidationSessionError):
        serialize_spec(CommandSpec("publish", argv))


def test_metadata_and_non_sensitive_env_alias_cannot_leak(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "V93_LONG_SECRET_VALUE")
    with pytest.raises(ValidationSessionError):
        serialize_spec(CommandSpec("x", ["true"], env={"SAFE":"V93_LONG_SECRET_VALUE"}))
    with pytest.raises(ValidationSessionError):
        serialize_spec(CommandSpec("x", ["true"], metadata={"label":"V93_LONG_SECRET_VALUE"}))


def test_safe_opaque_references_remain_serializable():
    row=serialize_spec(CommandSpec("safe", ["tool", "--secret", "@vault:key-1"], metadata={"reuse_secret_identities":["vault:key-1"]}))
    assert row["argv"][-1]=="@vault:key-1"
    assert row["metadata"]["reuse_secret_identities"]==["vault:key-1"]
