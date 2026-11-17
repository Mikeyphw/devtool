"""STORAGE-R1 native consumer integration and maintained source contracts."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from devtool.storage_layout import (
    artifact_registry_file, backup_history_dir, cleanup_receipt_read_dirs,
    cleanup_receipt_write_dir, repository_key, storage_inventory,
    validation_session_dir, run_catalog_dir,
)
from devtool.artifact_registry import registry_path
from devtool.core_runtime import private_run_root
from devtool.validation_sessions import session_root
from devtool.run_store import repository_storage_inventory
from devtool.cleanup_lifecycle import persist_cleanup_receipt
from devtool.artifacts import append_history, load_history
from devtool.repository_cleanup import _history_root


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME",str(tmp_path/"state"))
    monkeypatch.setenv("XDG_DATA_HOME",str(tmp_path/"data"))
    monkeypatch.delenv("DEVTOOL_XDG_DIR",raising=False)
    monkeypatch.delenv("DEVTOOL_VALIDATION_STATE_REPO_ROOT",raising=False)
    root=tmp_path/"project";root.mkdir()
    return root


def test_existing_consumer_resolvers_share_authority(repo: Path) -> None:
    assert private_run_root(repo)==run_catalog_dir(repo)
    assert session_root(repo)==validation_session_dir(repo)
    assert registry_path(repo)==artifact_registry_file(repo)
    assert _history_root(repo)==backup_history_dir(repo)
    assert all(not path.is_relative_to(repo) for path in (
        private_run_root(repo),session_root(repo),registry_path(repo),_history_root(repo)
    ))
    assert not (repo/".devtool").exists()


def test_receipt_writer_preserves_clean_checkout(repo: Path) -> None:
    receipt_id="cleanup-receipt:"+"a"*32
    receipt=SimpleNamespace(receipt_id=receipt_id,as_dict=lambda: {"receipt_id":receipt_id,"status":"applied"})
    location=persist_cleanup_receipt(repo,receipt)
    assert location.is_file()
    assert location.parent==cleanup_receipt_write_dir(repo)
    assert location.parent in cleanup_receipt_read_dirs(repo)
    assert not (repo/".devtool").exists()


def test_history_default_is_external_and_local_export_is_explicit(repo: Path) -> None:
    path=append_history(repo,{"history_id":"storage-r1-one","applied_at":"2026-11-16T00:00:00Z","status":"ok"})
    assert path.is_file() and not path.is_relative_to(repo)
    assert not (repo/".devtool").exists()
    assert any(row.get("history_id")=="storage-r1-one" for row in load_history(repo))
    local=append_history(repo,{"history_id":"storage-r1-two","status":"ok"},repo_local=True)
    assert local.is_relative_to(repo)
    assert any(row.get("history_id")=="storage-r1-two" for row in load_history(repo))


def test_inventory_recognizes_managed_generated_workspace(repo: Path) -> None:
    for family in ("reports","tmp","build","package","artifacts.d","artifacts"):
        (repo/".devtool"/family).mkdir(parents=True)
    report=repository_storage_inventory(repo,run_catalog_dir(repo))
    assert report["status"]=="pass"
    assert not report["unclassified"]
    assert report["storage_layout"]["safe_to_migrate"]
    (repo/".devtool"/"unexpected-user-data").mkdir()
    assert repository_storage_inventory(repo,run_catalog_dir(repo))["status"]=="fail"


def test_repo_ignore_rules_do_not_hide_config_source() -> None:
    root=Path(__file__).resolve().parents[1]
    ignore=(root/".gitignore").read_text(encoding="utf-8")
    assert "**/.devtool/" in ignore
    assert "Intentionally DO NOT ignore .devtool/artifacts.d" not in ignore
    assert "devtool-artifact.json\n" not in ignore
    assert "!**/.devtool/artifacts.d" not in ignore
    assert (root/".devtool.toml").is_file()


def test_worktree_identity_avoids_collisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first=tmp_path/"a"/"project";second=tmp_path/"b"/"project"
    first.mkdir(parents=True);second.mkdir(parents=True)
    assert repository_key(first)!=repository_key(second)


def test_previous_external_run_state_is_readable(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.xdg import state_dir
    monkeypatch.setenv("DEVTOOL_XDG_DIR",str(repo.parent/"old-xdg"))
    previous=state_dir()/"run-v3"/repository_key(repo)
    previous.mkdir(parents=True)
    (previous/"catalog.json").write_text('{"runs":{}}',encoding="utf-8")
    assert private_run_root(repo)==previous


def test_session_legacy_content_is_not_overwritten(repo: Path) -> None:
    previous=repo/".devtool/state/android-validation"
    previous.mkdir(parents=True)
    (previous/"active.json").write_text('{"session_id":"existing"}',encoding="utf-8")
    assert session_root(repo)==previous
    assert (previous/"active.json").read_text(encoding="utf-8")== '{"session_id":"existing"}'

# G3 - actual RUN and TEST authority functions must preserve migration fencing.
def test_migration_cutover_refuses_save_through_preexisting_catalog(repo: Path) -> None:
    from unittest.mock import patch
    from devtool.run_store import RunCatalog
    from devtool import storage_layout as sl
    legacy=repo/".devtool/private/run-v3"
    legacy.mkdir(parents=True)
    (legacy/"catalog.json").write_text('{"runs":{}}', encoding="utf-8")
    catalog=RunCatalog.open(private_run_root(repo),create=False)
    with patch("devtool.transactions.find_active_transactions",return_value=[]), \
         patch("devtool.validation_sessions.load_active_session",return_value=None):
        sl.migrate_storage(repo,confirmed=True)
    with pytest.raises(sl.StorageLayoutError,match="Stale storage writer"):
        catalog.save()
    assert sl.run_catalog_dir(repo)!=legacy


def test_native_life_exdev_archive_verifies_and_retires_source(repo: Path) -> None:
    from devtool.cleanup_lifecycle import _native_verified_cross_device_archive, _path_snapshot
    from unittest.mock import patch
    from devtool import storage_layout as sl
    source=repo/"generated-backup"
    (source/"nested").mkdir(parents=True)
    (source/"nested"/"evidence.txt").write_text("verified")
    destination=repo.parent/"data"/"backup-history"/"item"
    destination.parent.mkdir(parents=True,exist_ok=True)
    before=_path_snapshot(source)
    _native_verified_cross_device_archive(source,destination,before)
    assert not source.exists()
    assert (destination/"nested"/"evidence.txt").read_text()=="verified"


def test_native_life_exdev_copy_failure_retains_source(repo: Path,monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.cleanup_lifecycle import _native_verified_cross_device_archive, _path_snapshot
    import shutil
    source=repo/"backup-to-retain";source.mkdir()
    (source/"receipt.json").write_text('{"saved":true}')
    destination=repo.parent/"target"/"archive";destination.parent.mkdir()
    before=_path_snapshot(source)
    def fail_copy(*args,**kwargs):
        raise OSError("injected copy failure")
    monkeypatch.setattr(shutil,"copytree",fail_copy)
    with pytest.raises(OSError,match="injected"):
        _native_verified_cross_device_archive(source,destination,before)
    assert (source/"receipt.json").read_text()=='{"saved":true}'
    assert not destination.exists()


def test_life_archive_routes_exdev_to_verified_native_fallback(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import errno, os
    from devtool.repository_cleanup import archive_backup_candidates
    owned=repo/".diagnostic-surface-backup-exdev"
    owned.mkdir();(owned/"file.txt").write_text("unchanged",encoding="utf-8")
    actual_replace=os.replace
    def exdev_first(source,destination):
        if Path(source)==owned and Path(destination).is_relative_to(repo.parent/"data"):
            raise OSError(errno.EXDEV,"simulated cross-device rename")
        return actual_replace(source,destination)
    monkeypatch.setattr(os,"replace",exdev_first)
    moves=archive_backup_candidates(repo,stamp="20261116T000000Z")
    assert len(moves)==1
    archived=Path(moves[0].destination)
    assert archived.is_absolute()
    assert (archived/"file.txt").read_text(encoding="utf-8")=="unchanged"
    assert not owned.exists()


def test_native_life_exdev_symlink_source_is_refused(repo: Path) -> None:
    from devtool.cleanup_lifecycle import _native_verified_cross_device_archive, _path_snapshot
    source=repo/"backup-symlink";source.mkdir()
    outside=repo.parent/"external";outside.mkdir()
    (source/"external-link").symlink_to(outside,target_is_directory=True)
    destination=repo.parent/"target"/"backup";destination.parent.mkdir()
    with pytest.raises(OSError,match="Unsafe cross-device source tree"):
        _native_verified_cross_device_archive(source,destination,_path_snapshot(source))
    assert (source/"external-link").is_symlink() and not destination.exists()


def test_run_stream_writer_refuses_stale_legacy_root_after_migration(repo: Path) -> None:
    from unittest.mock import patch
    from devtool.run_store import write_bounded_stream
    from devtool import storage_layout as sl
    legacy=repo/".devtool/private/run-v3"
    legacy.mkdir(parents=True)
    (legacy/"catalog.json").write_text('{"runs":{}}',encoding="utf-8")
    assert private_run_root(repo)==legacy
    with patch("devtool.transactions.find_active_transactions",return_value=[]), \
         patch("devtool.validation_sessions.load_active_session",return_value=None):
        sl.migrate_storage(repo,confirmed=True)
    with pytest.raises(sl.StorageLayoutError,match="Stale storage writer"):
        write_bounded_stream(run_root=legacy,run_id="older",kind="stdout",content="stale")
    assert not (legacy/"streams").exists()
