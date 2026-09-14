package io.github.mikeceead.devtoolhud;

import android.app.IntentService;
import android.content.Intent;
import android.os.Bundle;

public final class TermuxResultService extends IntentService {
    static final String EXTRA_EXECUTION_ID = "execution_id";
    static final String EXTRA_RUN_ID = "run_id";
    static final String EXTRA_GENERATION = "generation";
    static final String EXTRA_TASKER_FIRE_INTENT = "tasker_fire_intent";
    static final String EXTRA_OVERLAY = "overlay";
    static final String EXTRA_BRIDGE_OPERATION = "bridge_operation";
    static final String EXTRA_BRIDGE_EXPECTED_HASH = "bridge_expected_hash";
    private static final String TAG = "DevtoolTermuxResult";

    public TermuxResultService() {
        super("DevtoolTermuxResult");
    }

    @Override
    protected void onHandleIntent(Intent intent) {
        if (intent == null) return;
        int executionId = intent.getIntExtra(EXTRA_EXECUTION_ID, -1);
        String runId = intent.getStringExtra(EXTRA_RUN_ID);
        if (runId == null || runId.trim().isEmpty()) runId = "termux-" + executionId;
        long generation = intent.getLongExtra(EXTRA_GENERATION, executionId);
        if (!matchesPendingIntentIdentity(runId, executionId, generation)) {
            String error = "Rejected stale or mismatched Termux result identity";
            DebugRecorder.warning(this, "termux", "TERMUX_RESULT_IDENTITY_REJECTED", "TermuxResultService",
                    runId, "", "", "", error,
                    DebugRecorder.details("execution_id", executionId, "generation", generation));
            return;
        }
        Bundle result = intent.getBundleExtra("result");
        Intent taskerFireIntent = getParcelableIntent(intent, EXTRA_TASKER_FIRE_INTENT);
        String bridgeOperation = intent.getStringExtra(EXTRA_BRIDGE_OPERATION);
        String bridgeExpectedHash = intent.getStringExtra(EXTRA_BRIDGE_EXPECTED_HASH);
        if (result == null) {
            String error = "Termux returned no result bundle";
            DebugRecorder.error(this, "termux", "TERMUX_RESULT_MISSING", "TermuxResultService",
                    runId, "", "", "", error,
                    DebugRecorder.details("execution_id", executionId, "bridge_operation", bridgeOperation));
            boolean overlay = intent.getBooleanExtra(EXTRA_OVERLAY, false);
            if (BridgeRoundtripProbe.OPERATION.equals(bridgeOperation)) {
                BridgeRoundtripProbe.handleResult(this, runId, executionId, -1, "", "", error);
                return;
            }
            if (bridgeOperation != null && bridgeOperation.startsWith("validation-")) {
                ValidationController.handleResult(this, bridgeOperation, -1, "", "", error, "native-termux");
                return;
            }
            if (bridgeOperation != null && bridgeOperation.startsWith("repository-")) {
                sendBroadcast(new Intent(RepositoryStore.ACTION_UPDATED).setPackage(getPackageName())
                        .putExtra(RepositoryStore.EXTRA_ERROR, error));
                return;
            }
            if (bridgeOperation != null && !bridgeOperation.isEmpty()) {
                BridgeTokenManager.handleResult(this, bridgeOperation, bridgeExpectedHash,
                        -1, "", "", error);
                return;
            }
            HudUpdate completed = HudUpdate.commandFinished(runId, -1, "", "", error, overlay);
            boolean richCompletion = RunStore.finish(this, runId, -1, "", "", error);
            if (!richCompletion) {
                new NotificationController(this).post(completed);
                OverlayHudService.publish(this, completed);
            }
            finishTasker(taskerFireIntent, executionId, -1, "", "", error);
            return;
        }
        String stdout = result.getString("stdout", "");
        String stderr = result.getString("stderr", "");
        int exitCode = result.getInt("exitCode", -1);
        int internalError = result.getInt("err", android.app.Activity.RESULT_OK);
        String errorMessage = result.getString("errmsg", "");
        String error = internalError == android.app.Activity.RESULT_OK ? "" :
                (errorMessage == null || errorMessage.trim().isEmpty()
                        ? "Termux internal error " + internalError : errorMessage);
        boolean overlay = intent.getBooleanExtra(EXTRA_OVERLAY, false);
        if (BridgeRoundtripProbe.OPERATION.equals(bridgeOperation)) {
            RunCommandTrace.record(this, RunCommandTrace.BRIDGE_ROUNDTRIP_RESULT_RECEIVED, "TermuxResultService",
                    runId, "Bridge roundtrip pending result received",
                    DebugRecorder.details("execution_id", executionId, "exit_code", exitCode,
                            "stdout_chars", stdout.length(), "stderr_chars", stderr.length(), "error", error));
            BridgeRoundtripProbe.handleResult(this, runId, executionId, exitCode, stdout, stderr, error);
            return;
        }
        if (bridgeOperation != null && bridgeOperation.startsWith("validation-")) {
            ValidationController.handleResult(this, bridgeOperation, exitCode, stdout, stderr, error, "native-termux");
            return;
        }
        if (bridgeOperation != null && bridgeOperation.startsWith("repository-")) {
            String utilityError = exitCode == 0 && error.isEmpty() ? ""
                    : (error.isEmpty() ? (stderr.isEmpty() ? stdout : stderr) : error);
            if (!utilityError.isEmpty()) {
                DebugRecorder.error(this, "repo", "REPOSITORY_UTILITY_FAILED", "TermuxResultService",
                        runId, "", "", "", utilityError,
                        DebugRecorder.details("execution_id", executionId, "exit_code", exitCode,
                                "bridge_operation", bridgeOperation));
            }
            sendBroadcast(new Intent(RepositoryStore.ACTION_UPDATED).setPackage(getPackageName())
                    .putExtra(RepositoryStore.EXTRA_ERROR, utilityError));
            return;
        }
        if (bridgeOperation != null && !bridgeOperation.isEmpty()) {
            BridgeTokenManager.handleResult(this, bridgeOperation, bridgeExpectedHash,
                    exitCode, stdout, stderr, error);
            return;
        }
        DebugRecorder.record(this, exitCode == 0 && error.isEmpty() ? "success" : "error", "termux",
                "TERMUX_RESULT_DELIVERED", "TermuxResultService", runId, "", "", "",
                "RunCommand result delivered",
                DebugRecorder.details("execution_id", executionId, "exit_code", exitCode,
                        "stdout_chars", stdout.length(), "stderr_chars", stderr.length(), "error", error));
        AndroidPrivacyLog.info(TAG, "Execution " + executionId + " run=" + runId + " finished with exit code " + exitCode);
        RunStore.appendDiagnostic(this, runId, "RunCommand result delivered: exit=" + exitCode
                + " stdout=" + stdout.length() + " chars stderr=" + stderr.length() + " chars");
        HudUpdate completed = HudUpdate.commandFinished(runId, exitCode, stdout, stderr, error, overlay);
        boolean richCompletion = RunStore.finish(this, runId, exitCode, stdout, stderr, error);
        if (!richCompletion) {
            new NotificationController(this).post(completed);
            OverlayHudService.publish(this, completed);
        }
        finishTasker(taskerFireIntent, executionId, exitCode, stdout, stderr, error);
    }

    private static boolean matchesPendingIntentIdentity(String runId, int executionId, long generation) {
        if (executionId <= 0 || generation != executionId) return false;
        return runId != null && runId.equals("termux-" + executionId);
    }

    private void finishTasker(Intent fireIntent, int executionId, int exitCode,
                              String stdout, String stderr, String error) {
        if (fireIntent == null) return;
        Bundle vars = TaskerProtocol.variables(executionId, exitCode, stdout, stderr, error);
        int resultCode = exitCode == 0 && error.isEmpty() ? TaskerProtocol.RESULT_OK : TaskerProtocol.RESULT_FAILED;
        TaskerProtocol.signalFinish(this, fireIntent, resultCode, vars);
    }

    @SuppressWarnings("deprecation")
    private static Intent getParcelableIntent(Intent source, String key) {
        if (android.os.Build.VERSION.SDK_INT >= 33) return source.getParcelableExtra(key, Intent.class);
        return source.getParcelableExtra(key);
    }
}
