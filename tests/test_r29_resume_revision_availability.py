from unittest import mock
import pytest
from devtool.federation_discovery import resolve_federated_repository
from devtool.federation_operations import (capture_federated_resume_checkpoint,
    validate_federated_resume_checkpoint,FederationOperationalBoundaryError)
from test_frx10_operational_boundaries import _parent,_load

def test_non_git_fixture_remains_usable(tmp_path):
    parent,_=_parent(tmp_path)
    config=_load(parent)
    checkpoint=capture_federated_resume_checkpoint(resolve_federated_repository(config,'child'))
    assert checkpoint['git_head']==''
    validate_federated_resume_checkpoint(config,checkpoint)

@pytest.mark.parametrize('old,new',[('a'*40,''),('','a'*40),('a'*40,'b'*40)])
def test_git_revision_missing_or_changed_is_not_accepted(tmp_path,monkeypatch,old,new):
    parent,_=_parent(tmp_path);config=_load(parent)
    monkeypatch.setattr('devtool.federation_operations._git_head',lambda root:old)
    checkpoint=capture_federated_resume_checkpoint(resolve_federated_repository(config,'child'))
    monkeypatch.setattr('devtool.federation_operations._git_head',lambda root:new)
    with pytest.raises(FederationOperationalBoundaryError,match='revision changed or is unavailable'):
        validate_federated_resume_checkpoint(config,checkpoint)

def test_same_revision_remains_valid(tmp_path,monkeypatch):
    parent,_=_parent(tmp_path);config=_load(parent)
    monkeypatch.setattr('devtool.federation_operations._git_head',lambda root:'a'*40)
    checkpoint=capture_federated_resume_checkpoint(resolve_federated_repository(config,'child'))
    validate_federated_resume_checkpoint(config,checkpoint)
