"""Regression closure of R36 validation and OCI consumer mismatch families."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from devtool.models import CommandSpec
from devtool.validation_sessions import ValidationSessionError, serialize_spec
from devtool.oci_image import canonical_android_oci_definition


def test_unrelated_short_ambient_credential_does_not_block_metadata(monkeypatch):
    monkeypatch.setenv('UNRELATED_PASSWORD', 'admin')
    spec = CommandSpec('ok', ['true'], metadata={'validation_administrative': True, 'status': 'administered'})
    assert serialize_spec(spec)['metadata'] == spec.metadata


def test_short_ambient_credential_as_standalone_metadata_still_rejected(monkeypatch):
    monkeypatch.setenv('UNRELATED_PASSWORD', 'admin')
    with pytest.raises(ValidationSessionError, match='metadata contains a credential'):
        serialize_spec(CommandSpec('no', ['true'], metadata={'value':'admin'}))


def test_spec_supplied_short_credential_cannot_alias_into_metadata(monkeypatch):
    monkeypatch.setenv('UNRELATED_PASSWORD', 'admin')
    with pytest.raises(ValidationSessionError, match='metadata contains a credential'):
        serialize_spec(CommandSpec('no', ['true'], env={'API_TOKEN': 'abcd'}, metadata={'value':'prefixabcd_suffix'}))


def test_oci_builder_digest_pinned_to_exact_canonical_embedded_copy():
    root=Path(__file__).resolve().parents[1]
    canonical=(root/'devtool/android/_standalone.py').read_bytes()
    embedded=(root/'devtool/resources/oci/android-arm64/build-apk').read_bytes()
    assert canonical == embedded
    definition=canonical_android_oci_definition()
    assert definition.contract['devtool_runtime']['embedded_android_builder']['sha256']==hashlib.sha256(canonical).hexdigest()
