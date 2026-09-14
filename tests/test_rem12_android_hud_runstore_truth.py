from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_bridge_and_runstore_share_generation_and_sequence_truth() -> None:
    hud = read("HudUpdate.java")
    record = read("RunRecord.java")
    store = read("RunStore.java")
    gate = read("BridgeUpdateGate.java")

    assert "final long generation;" in hud
    assert 'intent.getLongExtra("generation", intent.getLongExtra("execution_generation", 0L))' in hud
    assert "this.generation = Math.max(0L, generation);" in hud
    assert "long generation = 0L;" in record
    assert "long bridgeSequence = -1L;" in record
    assert 'value.put("generation", generation);' in record
    assert 'value.put("bridge_sequence", bridgeSequence);' in record
    assert 'record.generation = value.optLong("generation", 0L);' in record
    assert 'record.bridgeSequence = value.optLong("bridge_sequence", -1L);' in record
    assert "staleBridgeUpdate(record, update)" in store
    assert "update.generation < record.generation" in store
    assert "update.sequence == record.bridgeSequence && update.updatedAtMs <= record.lastProgressAtMs" in store
    assert 'private static final String KEY_ACTIVE_GENERATION = "active_generation";' in gate
    assert 'prefs.getLong("generation:" + runId, 0L)' in gate
    assert "generation < activeGeneration" in gate
    assert 'putLong(KEY_ACTIVE_GENERATION, Math.max(generation, activeGeneration))' in gate


def test_termux_pending_intent_identity_is_unique_and_rejected_when_stale() -> None:
    runner = read("TermuxCommandRunner.java")
    service = read("TermuxResultService.java")

    assert 'static final String ACTION_RESULT = "io.github.mikeceead.devtoolhud.TERMUX_RESULT";' in runner
    assert ".setAction(ACTION_RESULT + \".\" + executionId)" in runner
    assert ".setPackage(context.getPackageName())" in runner
    assert "PendingIntent.FLAG_ONE_SHOT | PendingIntent.FLAG_CANCEL_CURRENT" in runner
    assert "PendingIntent.FLAG_UPDATE_CURRENT" in runner
    assert "TermuxResultService.EXTRA_GENERATION" in runner
    assert 'static final String EXTRA_GENERATION = "generation";' in service
    assert "matchesPendingIntentIdentity(runId, executionId, generation)" in service
    assert 'runId.equals("termux-" + executionId)' in service
    assert '"TERMUX_RESULT_IDENTITY_REJECTED"' in service


def test_root_bridge_execution_is_bounded_retried_and_queue_safe() -> None:
    root = read("RootBridgeManager.java")
    broker = read("RootCommandBroker.java")

    assert "new ThreadPoolExecutor(" in root
    assert "new LinkedBlockingQueue<Runnable>(32)" in root
    assert "RejectedExecutionException" in root
    assert '"ROOT_BRIDGE_EXECUTOR_REJECTED"' in root
    assert "private static boolean submit(Context context, String failureDetail, Runnable task)" in root
    assert "isTransientSuFailure" in root
    assert "device or resource busy" in root
    assert "temporarily unavailable" in root
    assert "rootCommandOnce(shell, timeoutSeconds)" in root
    assert "private static final int MAX_OUTPUT = 64_000;" in root
    assert "if (output.size() > MAX_OUTPUT)" in root
    assert "private static final int MAX_OUTPUT = 2 * 1024 * 1024;" in broker
    assert "processTreeCleared" in broker


def test_token_lifecycle_and_large_log_paging_are_finite() -> None:
    secrets = read("BridgeSecrets.java")
    tokens = read("BridgeTokenManager.java")
    indexer = read("RootLogIndexer.java")
    hud = read("HudUpdate.java")

    assert 'private static final String KEY_GENERATION = "generation";' in secrets
    assert "static long generation(Context context)" in secrets
    assert "putLong(KEY_GENERATION, nextGeneration)" in secrets
    assert 'private static final String KEY_VERIFIED_GENERATION = "verified_generation";' in tokens
    assert "BridgeSecrets.generation(context)" in tokens
    assert ".putLong(KEY_VERIFIED_GENERATION, BridgeSecrets.generation(context))" in tokens
    assert "MAX_SCAN_BYTES_PER_FILE" in indexer
    assert "cursor.byteOffset - scanStart < MAX_SCAN_BYTES_PER_FILE" in indexer
    assert "final int maxStepsForUi = 80;" in hud
    assert "i < array.length() && i < maxStepsForUi" in hud
