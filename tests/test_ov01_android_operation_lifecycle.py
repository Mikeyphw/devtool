from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_mr004_operation_root_itself_has_marker_in_initial_environment() -> None:
    broker = read("RootCommandBroker.java")
    execute = broker[broker.index("private CommandResult executeRoot"):broker.index("private static void copyBounded")]
    assert "exec sh -c " in execute
    assert "shellQuote(shell)" in execute
    assert "initial" in execute and "/proc/<pid>/environ" in execute


def test_mr004_timeout_and_cancel_both_clear_marked_operation_tree() -> None:
    broker = read("RootCommandBroker.java")
    execute = broker[broker.index("private CommandResult executeRoot"):broker.index("private static void copyBounded")]
    assert "stopExecutionTree(process)" in execute
    assert "processTreeCleared" in execute
    assert "cancelled || timedOut" in execute or "!finished" in execute


def test_mr004_termination_is_verified_not_fire_and_forget() -> None:
    broker = read("RootCommandBroker.java")
    terminate = broker[broker.index("boolean terminateOperationProcess"):broker.index("CommandResult execute(")]
    assert "operationProcessState(marker) == OperationProcessState.ABSENT" in terminate
    assert "kill -KILL" in terminate


def test_mr004_process_scan_failure_is_not_treated_as_absence() -> None:
    broker = read("RootCommandBroker.java")
    state = broker[broker.index("private OperationProcessState operationProcessState"):broker.index("boolean terminateOperationProcess")]
    assert "OperationProcessState.UNKNOWN" in state
    assert "result.timedOut || result.exitCode != 0" in state
    assert "operationProcessState(requestedOperationId) != OperationProcessState.ABSENT" in state


def test_mr004_final_tree_verification_happens_after_wrapper_termination() -> None:
    broker = read("RootCommandBroker.java")
    helper = broker[broker.index("private boolean stopExecutionTree"):broker.index("CommandResult execute(")]
    wrapper_destroy = helper.index("process.destroy()")
    final_verify = helper.rfind("terminateOperationProcess(operationId)")
    assert final_verify > wrapper_destroy
    assert "boolean cleared = operationId.isEmpty() || terminateOperationProcess(operationId);" in helper


def test_mr004_normal_completion_also_proves_no_marked_descendant_survives() -> None:
    broker = read("RootCommandBroker.java")
    execute = broker[broker.index("private CommandResult executeRoot"):broker.index("private static void copyBounded")]
    assert "ensureOperationProcessTreeGone()" in execute
    assert "background descendant remains" in execute
    assert "processTreeCleared" in execute


def test_mr004_exception_cleanup_cannot_default_to_process_tree_cleared() -> None:
    broker = read("RootCommandBroker.java")
    execute = broker[broker.index("private CommandResult executeRoot"):broker.index("private static void copyBounded")]
    catch = execute[execute.index("} catch (Exception error)"):]
    assert "stopExecutionTree(process)" in catch
    assert "processTreeCleared" in catch
    assert "false, false, false, processTreeCleared" in catch


def test_mr004_repository_queue_blocks_while_live_orphan_survives() -> None:
    workspace = read("RepositoryWorkspaceStore.java")
    schedule = workspace[workspace.index("private static void scheduleNext"):workspace.index("private static void executeQueued")]
    assert "liveOrphanBlocksQueue" in schedule
    execute = workspace[workspace.index("private static void executeQueued"):workspace.index("private static void completeCancelled")]
    assert "processTreeCleared" in execute
    assert 'state = "orphaned"' in execute
    assert "scheduleNext" in execute


def test_mr004_orphan_recovery_never_releases_queue_until_process_is_gone() -> None:
    workspace = read("RepositoryWorkspaceStore.java")
    cancel = workspace[workspace.index("static boolean cancel"):workspace.index("static boolean terminateOrphan")]
    assert 'orphaned.state = "orphaned"' in cancel
    assert "scheduleNext(app, id)" in cancel
    mark = workspace[workspace.index("static boolean markInterrupted"):workspace.index("static boolean retry")]
    assert "operationProcessAlive(wanted)" in mark
