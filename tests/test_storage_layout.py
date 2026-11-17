"""STORAGE-R1 retained safety/recovery/Git-ignore regression tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from devtool import storage_layout as sl


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("DEVTOOL_XDG_DIR",raising=False)
    monkeypatch.delenv("DEVTOOL_VALIDATION_STATE_REPO_ROOT",raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def test_families_are_unique_and_owned() -> None:
    assert len(sl.FAMILIES) == len(sl.FAMILY_MAP)
    assert {x.name for x in sl.FAMILIES} == set(sl.FAMILY_MAP)
    assert all(x.owner and x.class_name and x.legacy_relative for x in sl.FAMILIES)


def test_new_repo_uses_xdg_without_writing_worktree(root: Path) -> None:
    for name in sl.FAMILY_MAP:
        current = sl.family_dir(root, name)
        assert current == sl.external_family_dir(root, name)
        assert not current.exists()
    assert not (root / ".devtool").exists()
    row = sl.storage_inventory(root)
    assert row["safe_to_migrate"]
    assert row["mutates"] is False


def test_explicit_xdg_override_is_respected(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    external = root.parent / "private-storage"
    monkeypatch.setenv("DEVTOOL_XDG_DIR",str(external))
    assert sl.cleanup_receipt_write_dir(root).is_relative_to(external)
    assert sl.backup_history_dir(root).is_relative_to(external)
    assert not (root / ".devtool").exists()


def test_legacy_run_catalog_is_read_until_explicit_migration(root: Path) -> None:
    prior = root / ".devtool/private/run-v3"
    prior.mkdir(parents=True)
    (prior / "catalog.json").write_text('{"runs":{}}',encoding="utf-8")
    assert sl.run_catalog_dir(root) == prior
    assert sl.storage_inventory(root)["families"][0]["needs_migration"]


def test_split_brain_run_catalog_fails_closed(root: Path) -> None:
    old = root / ".devtool/private/run-v3"
    new = sl.external_family_dir(root,"run-v3")
    for target in (old,new):
        target.mkdir(parents=True)
        (target / "catalog.json").write_text("{}",encoding="utf-8")
    with pytest.raises(sl.StorageLayoutError,match="both contain data"):
        sl.run_catalog_dir(root)


def test_legacy_xdg_run_convention_is_still_readable(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.xdg import state_dir
    monkeypatch.setenv("DEVTOOL_XDG_DIR",str(root.parent / "old-override"))
    old = state_dir() / "run-v3" / sl.repository_key(root)
    old.mkdir(parents=True)
    (old / "catalog.json").write_text("{}",encoding="utf-8")
    assert sl.run_catalog_dir(root)==old


def test_receipts_write_external_and_read_both_histories(root: Path) -> None:
    legacy = root / ".devtool/private/cleanup/receipts"
    legacy.mkdir(parents=True)
    (legacy / "cleanup-receipt-old.json").write_text("{}",encoding="utf-8")
    assert sl.cleanup_receipt_write_dir(root) != legacy
    assert legacy in sl.cleanup_receipt_read_dirs(root)
    assert sl.cleanup_receipt_write_dir(root) in sl.cleanup_receipt_read_dirs(root)
    assert sl.storage_inventory(root)["safe_to_migrate"]


def test_legacy_validation_and_registry_preserved(root: Path) -> None:
    legacy = root / ".devtool/state/android-validation"
    legacy.mkdir(parents=True)
    (legacy / "active.json").write_text("{}",encoding="utf-8")
    assert sl.validation_session_dir(root)==legacy
    registry = root / ".devtool/artifacts"
    registry.mkdir(parents=True)
    (registry / "registry.json").write_text("{}",encoding="utf-8")
    assert sl.artifact_registry_file(root)==registry/"registry.json"


def test_explicit_validation_redirection_retained(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    external = root.parent / "guest-state"
    monkeypatch.setenv("DEVTOOL_VALIDATION_STATE_REPO_ROOT",str(external))
    assert sl.validation_session_dir(root)==external/".devtool/state/android-validation"


def test_migration_requires_confirmation_without_mutation(root: Path) -> None:
    legacy = root / ".devtool/private/run-v3"
    legacy.mkdir(parents=True)
    (legacy / "catalog.json").write_text("{}",encoding="utf-8")
    with pytest.raises(sl.StorageLayoutError,match="explicit --yes"):
        sl.migrate_storage(root)
    assert not sl.external_family_dir(root,"run-v3").exists()


def test_symlinked_legacy_ancestor_is_refused(root: Path) -> None:
    outside = root.parent/"other"
    outside.mkdir()
    (root/".devtool").symlink_to(outside,target_is_directory=True)
    with pytest.raises(sl.StorageLayoutError,match="Symlinked"):
        sl.run_catalog_dir(root)


def test_symlinked_migration_member_is_refused(root: Path) -> None:
    source = root/".devtool/private/run-v3"
    source.mkdir(parents=True)
    (source/"alias").symlink_to(root.parent)
    with pytest.raises(sl.StorageLayoutError,match="Symlinked"):
        sl._fingerprint_dir(source)


def test_storage_doctor_classifies_known_outputs_and_unknowns(root: Path) -> None:
    for name in ("tmp","reports","build","package","artifacts.d","artifacts","history"):
        (root/".devtool"/name).mkdir(parents=True)
    first = sl.storage_inventory(root)
    assert not first["unknown_local_families"]
    (root/".devtool"/"surprise").mkdir()
    assert sl.storage_inventory(root)["unknown_local_families"]==["surprise"]


def test_tracked_local_runtime_files_are_reported_not_deleted(root: Path) -> None:
    subprocess.run(["git","init","-q",str(root)],check=True)
    (root/".devtool").mkdir()
    (root/".devtool"/"user.txt").write_text("preserve",encoding="utf-8")
    subprocess.run(["git","-C",str(root),"add","-f",".devtool/user.txt"],check=True)
    inv=sl.storage_inventory(root)
    assert ".devtool/user.txt" in inv["tracked_local_files"]
    assert (root/".devtool/user.txt").read_text()=="preserve"


def test_recovery_can_finish_identical_interrupted_publication(root: Path) -> None:
    # Simulate a crash after verified os.replace but before migration marker.
    source = root/".devtool/private/run-v3"
    source.mkdir(parents=True)
    (source/"catalog.json").write_text('{"runs":{}}',encoding="utf-8")
    target = sl.external_family_dir(root,"run-v3")
    sl.ensure_private_storage_dir(target.parent)
    shutil.copytree(source,target)
    # Quiescence is independently checked by native modules in production;
    # fixture substitutes the active-authority probes with empty evidence.
    with patch("devtool.transactions.find_active_transactions",return_value=[]), \
         patch("devtool.validation_sessions.load_active_session",return_value=None), \
         patch("devtool.run_store.RunCatalog.open",return_value=SimpleNamespace(runs={})):
        out=sl.migrate_storage(root,confirmed=True)
    assert any(x["family"]=="run-v3" for x in out["migrated"])
    assert sl.run_catalog_dir(root)==target
    assert source.exists()  # migration is never a deletion


def test_migration_refuses_conflicting_destination_without_overwrite(root: Path) -> None:
    source=root/".devtool/private/run-v3";source.mkdir(parents=True)
    (source/"catalog.json").write_text("original",encoding="utf-8")
    target=sl.external_family_dir(root,"run-v3");sl.ensure_private_storage_dir(target)
    (target/"catalog.json").write_text("other",encoding="utf-8")
    with patch("devtool.transactions.find_active_transactions",return_value=[]), \
         patch("devtool.validation_sessions.load_active_session",return_value=None), \
         patch("devtool.run_store.RunCatalog.open",return_value=SimpleNamespace(runs={})):
        with pytest.raises(sl.StorageLayoutError,match="differs"):
            sl.migrate_storage(root,confirmed=True)
    assert (source/"catalog.json").read_text()=="original"
    assert (target/"catalog.json").read_text()=="other"


def test_migration_refuses_live_transactions(root: Path) -> None:
    with patch("devtool.transactions.find_active_transactions",return_value=[root/"tx"]):
        with pytest.raises(sl.StorageLayoutError,match="Active LIFE"):
            sl.migrate_storage(root,confirmed=True)


def test_migration_refuses_active_validation(root: Path) -> None:
    with patch("devtool.transactions.find_active_transactions",return_value=[]), \
         patch("devtool.validation_sessions.load_active_session",return_value=SimpleNamespace(status="running")):
        with pytest.raises(sl.StorageLayoutError,match="Active validation"):
            sl.migrate_storage(root,confirmed=True)


def test_read_only_audit_does_not_create_runtime_files(root: Path) -> None:
    before=set(root.iterdir())
    assert sl.main(["audit","--repo",str(root)])==0
    assert set(root.iterdir())==before


def test_cleanup_receipts_migration_merges_without_overwriting(root: Path) -> None:
    legacy=root/".devtool/private/cleanup/receipts"
    legacy.mkdir(parents=True)
    (legacy/"cleanup-receipt-old.json").write_text('{"receipt_id":"old"}',encoding="utf-8")
    current=sl.cleanup_receipt_write_dir(root);sl.ensure_private_storage_dir(current)
    (current/"cleanup-receipt-new.json").write_text('{"receipt_id":"new"}',encoding="utf-8")
    with patch("devtool.transactions.find_active_transactions",return_value=[]), \
         patch("devtool.validation_sessions.load_active_session",return_value=None), \
         patch("devtool.run_store.RunCatalog.open",return_value=SimpleNamespace(runs={})):
        sl.migrate_storage(root,confirmed=True)
    assert (current/"cleanup-receipt-old.json").exists()
    assert (current/"cleanup-receipt-new.json").exists()
    assert (legacy/"cleanup-receipt-old.json").exists()


def test_migration_refuses_same_name_different_receipt(root: Path) -> None:
    legacy=root/".devtool/private/cleanup/receipts";legacy.mkdir(parents=True)
    current=sl.cleanup_receipt_write_dir(root);sl.ensure_private_storage_dir(current)
    for p,text in ((legacy,"old"),(current,"new")):
        (p/"cleanup-receipt-x.json").write_text(text,encoding="utf-8")
    with patch("devtool.transactions.find_active_transactions",return_value=[]), \
         patch("devtool.validation_sessions.load_active_session",return_value=None), \
         patch("devtool.run_store.RunCatalog.open",return_value=SimpleNamespace(runs={})):
        with pytest.raises(sl.StorageLayoutError,match="Conflicting cleanup receipt"):
            sl.migrate_storage(root,confirmed=True)
    assert (legacy/"cleanup-receipt-x.json").read_text()=="old"
    assert (current/"cleanup-receipt-x.json").read_text()=="new"


def test_ignore_audit_reports_actual_git_state(root: Path) -> None:
    subprocess.run(["git","init","-q",str(root)],check=True)
    first=sl.storage_inventory(root)
    assert first["git_ignore_status"]=="not-ignored"
    assert first["recommended_gitignore"]=="**/.devtool/"
    (root/".gitignore").write_text("**/.devtool/\n",encoding="utf-8")
    second=sl.storage_inventory(root)
    assert second["git_ignore_status"]=="ignored"
    assert sl.main(["ignore","--repo",str(root)])==0

# STORAGE-R1 corrected acceptance gaps G1-G3 and G5-G7. These are not tests
# that merely inspect source strings: they exercise real files and process locks.
def _idle_authorities():
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("devtool.transactions.find_active_transactions", return_value=[]))
    stack.enter_context(patch("devtool.validation_sessions.load_active_session", return_value=None))
    stack.enter_context(patch("devtool.run_store.RunCatalog.open", return_value=SimpleNamespace(runs={})))
    return stack


def test_g1_migrated_0755_directory_is_private_and_writable(root: Path) -> None:
    import stat
    source = root / ".devtool/private/run-v3"
    (source / "nested").mkdir(parents=True)
    (source / "catalog.json").write_text('{"runs":{}}', encoding="utf-8")
    (source / "nested" / "ev.json").write_text("{}", encoding="utf-8")
    source.chmod(0o755)
    (source / "nested").chmod(0o755)
    with _idle_authorities():
        assert sl.migrate_storage(root, confirmed=True)["migrated"]
    target = sl.external_family_dir(root, "run-v3")
    assert (stat.S_IMODE(target.stat().st_mode),stat.S_IMODE((target/"nested").stat().st_mode)) == (0o700,0o700)
    sl.ensure_private_storage_dir(target)
    (target / "additional.json").write_text("{}",encoding="utf-8")
    assert (source / "catalog.json").is_file()


def test_g2_xdg_symlinked_repositories_parent_rejected_during_read(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    from devtool.xdg import state_dir
    prefix = state_dir()
    prefix.mkdir(parents=True)
    elsewhere = root.parent / "elsewhere"
    elsewhere.mkdir()
    (prefix / "repositories").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(sl.StorageLayoutError, match="Unsafe XDG storage ancestor"):
        sl.run_catalog_dir(root)
    assert list(elsewhere.iterdir()) == []


def test_g3_concurrent_legacy_writer_removes_false_success_marker(root: Path) -> None:
    source = root / ".devtool/private/run-v3"
    source.mkdir(parents=True)
    (source / "catalog.json").write_text('{"runs":{}}', encoding="utf-8")
    write_atomic = sl._atomic_json
    def race(path, doc):
        write_atomic(path,doc)
        if doc.get("family") == "run-v3":
            (source / "catalog.json").write_text('{"runs":{"late":{"status":"running"}}}',encoding="utf-8")
    with _idle_authorities(), patch.object(sl,"_atomic_json",side_effect=race):
        with pytest.raises(sl.StorageLayoutError,match="Concurrent legacy writer"):
            sl.migrate_storage(root,confirmed=True)
    assert not sl._marker(root,"run-v3").exists(), "unsafe cutover marker must be withdrawn"
    with pytest.raises(sl.StorageLayoutError,match="both contain data"):
        sl.run_catalog_dir(root)


def test_g3_writer_lock_serializes_migration_and_rejects_stale_writer(root: Path) -> None:
    import threading
    source = root / ".devtool/private/run-v3"
    source.mkdir(parents=True)
    (source / "catalog.json").write_text('{"runs":{}}',encoding="utf-8")
    assert sl.run_catalog_dir(root) == source
    entered=threading.Event(); release=threading.Event()
    def writer():
        with sl.storage_writer_guard_for_path(source):
            entered.set();release.wait(timeout=5)
    t=threading.Thread(target=writer,daemon=True);t.start()
    assert entered.wait(timeout=3)
    try:
        with _idle_authorities():
            with pytest.raises(sl.StorageLayoutError,match="already active"):
                sl.migrate_storage(root,confirmed=True)
    finally:
        release.set();t.join(timeout=3)
    assert not t.is_alive()
    with _idle_authorities():
        sl.migrate_storage(root,confirmed=True)
    with pytest.raises(sl.StorageLayoutError,match="Stale storage writer"):
        with sl.storage_writer_guard_for_path(source):
            (source / "catalog.json").write_text('bad',encoding="utf-8")
    target=sl.run_catalog_dir(root)
    with sl.storage_writer_guard_for_path(target):
        (target / "new.json").write_text("{}",encoding="utf-8")
    assert (source / "catalog.json").read_text(encoding="utf-8")=='{"runs":{}}'


def test_g5_tracked_generated_files_block_readiness_and_plan(root: Path) -> None:
    subprocess.run(["git","init","-q",str(root)],check=True)
    local=root/".devtool/reports";local.mkdir(parents=True)
    (local/"tracked.txt").write_text("user-owned")
    subprocess.run(["git","-C",str(root),"add","-f",".devtool/reports/tracked.txt"],check=True)
    inventory=sl.storage_inventory(root)
    assert not inventory["safe_to_migrate"]
    plan=sl.migration_plan(root)
    assert not plan["ready"] and "tracked-local-files" in plan["blockers"]
    assert (local/"tracked.txt").read_text()=="user-owned"


def test_g6_plan_is_actionable_and_readonly(root: Path) -> None:
    local=root/".devtool/private/run-v3";local.mkdir(parents=True)
    (local/"catalog.json").write_text("{}",encoding="utf-8")
    before=set(root.rglob("*"))
    plan=sl.migration_plan(root)
    assert plan["operation"]=="migration-plan" and plan["mutates"] is False
    run=next(row for row in plan["actions"] if row["family"]=="run-v3")
    assert run["action"]=="copy-verify-and-cutover"
    assert run["estimated_files"]==1 and run["estimated_bytes"]==2
    assert run["sources"]==[str(local)]
    assert run["destination"]==str(sl.external_family_dir(root,"run-v3"))
    assert run["conflict_policy"]=="never-overwrite-different-bytes"
    assert set(root.rglob("*"))==before


def test_g7_custom_xdg_inside_repo_rejected_before_any_write(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom=root/"unignored-xdg"
    monkeypatch.setenv("DEVTOOL_XDG_DIR",str(custom))
    with pytest.raises(sl.StorageLayoutError,match="XDG storage inside target repository"):
        sl.run_catalog_dir(root)
    with pytest.raises(sl.StorageLayoutError,match="XDG storage inside target repository"):
        sl.migrate_storage(root,confirmed=True)
    assert not custom.exists(), "Refusal must not create Git worktree noise"


def test_g3_registered_writer_guard_blocks_stale_destination(root: Path) -> None:
    # Post-publication marker is the authority switch, even if a RunCatalog
    # object was opened against a legacy path before migration.
    source=root/".devtool/private/run-v3"
    source.mkdir(parents=True)
    (source/"catalog.json").write_text('{}',encoding="utf-8")
    sl.run_catalog_dir(root)
    with _idle_authorities():
        sl.migrate_storage(root,confirmed=True)
    assert sl.run_catalog_dir(root)==sl.external_family_dir(root,"run-v3")
    with pytest.raises(sl.StorageLayoutError, match="Stale storage writer"):
        with sl.storage_writer_guard_for_path(source):
            pass


def test_plan_detects_active_validation_without_touching_marker(root: Path) -> None:
    marker=root/".devtool/state/android-validation/active.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"status":"running", "session_id":"live"}',encoding="utf-8")
    before=marker.read_bytes()
    audit=sl.storage_inventory(root)
    assert not audit["safe_to_migrate"]
    assert "active-validation-marker" in audit["active_blockers"]
    plan=sl.migration_plan(root)
    assert not plan["ready"] and "active-validation-marker" in plan["blockers"]
    assert marker.read_bytes()==before


def test_plan_detects_corrupt_run_catalog_and_recovery_marker_readonly(root: Path) -> None:
    catalog=root/".devtool/private/run-v3/catalog.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text("{broken",encoding="utf-8")
    plan=sl.migration_plan(root)
    assert "unreadable-run-catalog" in plan["blockers"]
    assert catalog.read_text(encoding="utf-8")=="{broken"
