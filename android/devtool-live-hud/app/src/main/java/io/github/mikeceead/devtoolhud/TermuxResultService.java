package io.github.mikeceead.devtoolhud;

import android.app.IntentService;
import android.content.Intent;
import android.os.Bundle;
import android.util.Log;

public final class TermuxResultService extends IntentService {
    static final String EXTRA_EXECUTION_ID = "execution_id";
    static final String EXTRA_TASKER_FIRE_INTENT = "tasker_fire_intent";
    static final String EXTRA_OVERLAY = "overlay";
    private static final String TAG = "DevtoolTermuxResult";

    public TermuxResultService() {
        super("DevtoolTermuxResult");
    }

    @Override
    protected void onHandleIntent(Intent intent) {
        if (intent == null) return;
        int executionId = intent.getIntExtra(EXTRA_EXECUTION_ID, -1);
        Bundle result = intent.getBundleExtra("result");
        Intent taskerFireIntent = getParcelableIntent(intent, EXTRA_TASKER_FIRE_INTENT);
        if (result == null) {
            String error = "Termux returned no result bundle";
            boolean overlay = intent.getBooleanExtra(EXTRA_OVERLAY, false);
            HudUpdate completed = HudUpdate.commandFinished(executionId, -1, "", "", error, overlay);
            new NotificationController(this).post(completed);
            OverlayHudService.publish(this, completed);
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
        Log.i(TAG, "Execution " + executionId + " finished with exit code " + exitCode);
        HudUpdate completed = HudUpdate.commandFinished(executionId, exitCode, stdout, stderr, error, overlay);
        new NotificationController(this).post(completed);
        OverlayHudService.publish(this, completed);
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
