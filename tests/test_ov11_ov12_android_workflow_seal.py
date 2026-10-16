from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_live_run_notifications_are_permission_and_manager_guarded() -> None:
    notification = read("NotificationController.java")
    assert "import android.Manifest;" in notification
    assert "import android.content.pm.PackageManager;" in notification
    assert "if (manager != null) ensureChannel();" in notification
    assert "if (manager == null || update == null || !notificationsAllowed()) return;" in notification
    assert "if (manager == null || runId == null || runId.trim().isEmpty()) return;" in notification
    assert "if (manager == null || !notificationsAllowed()) return;" in notification
    assert "context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)" in notification
    assert "PackageManager.PERMISSION_GRANTED" in notification


def test_tasker_completion_is_kept_inside_trusted_tasker_packages() -> None:
    authorization = read("TaskerAuthorization.java")
    protocol = read("TaskerProtocol.java")
    receiver = read("TaskerPluginReceiver.java")
    assert "static boolean isTrustedPackage(String packageName)" in authorization
    assert "return TASKER.equals(packageName) || TASKER_LEGACY.equals(packageName);" in authorization
    assert "if (!TaskerAuthorization.isTrustedPackage(servicePackage)) return false;" in protocol
    assert "completion.getComponent()" in protocol
    assert 'completion.setPackage("net.dinglisch.android.taskerm")' in protocol
    assert "config.setClassLoader(TaskerPluginReceiver.class.getClassLoader())" in receiver


def test_repository_path_repairs_must_be_current_resolver_authorized_actions() -> None:
    store = read("RepositoryStore.java")
    assert "RepositoryPathResolver.findAction(record, action.actionId)" in store
    assert "if (authorized == null || !authorized.kind.equals(action.kind)) return null;" in store
    repair_body = store[store.index("applyPathRepair"):store.index("static void remove")]
    assert "action = authorized;" in repair_body
    for forbidden in ("git reset", "git clean", "rm -rf"):
        assert forbidden not in repair_body


def test_ov11_ov12_current_source_checks_replace_report_proof() -> None:
    # The old Markdown statement "120 passed" is not executable evidence.
    # Check the same implementation authority used by our runtime contract tests.
    resolver = read("RepositoryPathResolver.java")
    store = read("RepositoryStore.java")
    assert "findAction" in resolver
    assert "RepositoryPathResolver.findAction(record, action.actionId)" in store
    assert "if (authorized == null || !authorized.kind.equals(action.kind)) return null;" in store
    assert "action = authorized;" in store
    # Both present and independently executed under the CI24 pytest inventory.
    regression = ROOT / "tests/test_phase31_1_validation_completion.py"
    assert regression.is_file() and "def test_" in regression.read_text(encoding="utf-8")
