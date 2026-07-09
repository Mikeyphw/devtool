package io.github.mikeceead.devtoolhud;

import android.app.IntentService;
import android.content.Intent;
import android.os.Bundle;
import android.util.Log;

public final class TermuxResultService extends IntentService {
    static final String EXTRA_EXECUTION_ID = "execution_id";
    static final String EXTRA_RUN_ID = "run_id";
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
        Bundle result = intent.getBundleExtra("result");
        Intent taskerFireIntent = getParcelableIntent(intent, EXTRA_TASKER_FIRE_INTENT);
        String bridgeOperation = intent.getStringExtra(EXTRA_BRIDGE_OPERATION);
        String bridgeExpectedHash = intent.getStringExtra(EXTRA_BRIDGE_EXPECTED_HASH);
        if (result == null) {
            String error = "Termux returned no result bundle";
            boolean overlay = intent.getBooleanExtra(EXTRA_OVERLAY, false);
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
        if (bridgeOperation != null && !bridgeOperation.isEmpty()) {
            BridgeTokenManager.handleResult(this, bridgeOperation, bridgeExpectedHash,
                    exitCode, stdout, stderr, error);
            return;
        }
        Log.i(TAG, "Execution " + executionId + " finished with exit code " + exitCode);
        HudUpdate completed = HudUpdate.commandFinished(runId, exitCode, stdout, stderr, error, overlay);
        boolean richCompletion = RunStore.finish(this, runId, exitCode, stdout, stderr, error);
        if (!richCompletion) {
            new NotificationController(this).post(completed);
            OverlayHudService.publish(this, completed);
        }
        finishTasker(taskerFireIntent, executionId, exitCode, stdout, stderr, error);
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
