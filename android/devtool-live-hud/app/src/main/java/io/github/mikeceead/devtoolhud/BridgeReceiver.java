package io.github.mikeceead.devtoolhud;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;
import android.widget.Toast;

public final class BridgeReceiver extends BroadcastReceiver {
    private static final String TAG = "DevtoolHudBridge";
    private static final String TOAST_PREFS = "bridge_toasts";
    private static final int MAX_TOAST_LENGTH = 260;

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) return;
        String action = intent.getAction() == null ? "" : intent.getAction();
        String runId = intent.getStringExtra("run_id");
        if (!BridgeSecrets.accepts(context, intent.getStringExtra("token"))) {
            DebugRecorder.warning(context, "bridge", "BRIDGE_TOKEN_REJECTED", "BridgeReceiver",
                    runId, "", "", "", "Rejected update with an invalid bridge token",
                    DebugRecorder.details("action", action));
            Log.w(TAG, "Rejected update with an invalid bridge token");
            return;
        }

        if (!isExpectedBridgeAction(context, action)) {
            DebugRecorder.warning(context, "bridge", "BRIDGE_ACTION_REJECTED", "BridgeReceiver",
                    runId, "", "", "", "Rejected bridge update with unexpected action",
                    DebugRecorder.details("action", action));
            Log.w(TAG, "Rejected bridge update with unexpected action " + action);
            return;
        }
        if (action.endsWith("TOAST")) {
            showToast(context, intent.getStringExtra("toast"));
            return;
        }
        if (runId == null || runId.trim().isEmpty()) {
            DebugRecorder.warning(context, "bridge", "BRIDGE_RUN_ID_MISSING", "BridgeReceiver",
                    "", "", "", "", "Rejected bridge update without a run id",
                    DebugRecorder.details("action", action));
            Log.w(TAG, "Rejected bridge update without a run id");
            return;
        }
        if (!action.endsWith("CANCEL") && !BridgeUpdateGate.accept(context, intent)) {
            DebugRecorder.info(context, "bridge", "BRIDGE_UPDATE_STALE", "BridgeReceiver",
                    runId, "", "", "", "Ignored stale or out-of-order update",
                    DebugRecorder.details("action", action, "sequence", intent.getLongExtra("sequence", 0L)));
            Log.d(TAG, "Ignored stale or out-of-order update for run " + runId);
            return;
        }
        NotificationController controller = new NotificationController(context);
        if (action.endsWith("CANCEL")) {
            DebugRecorder.warning(context, "bridge", "BRIDGE_CANCEL_ACCEPTED", "BridgeReceiver",
                    runId, "", "", "", "Bridge cancellation accepted", DebugRecorder.details("action", action));
            RunStore.cancelled(context, runId);
            controller.cancel(runId);
            OverlayHudService.cancel(runId);
            return;
        }

        String traceCheckpoint = RunCommandTrace.fromBridgeIntent(intent);
        if (!traceCheckpoint.isEmpty() && !"RUN_TRACE_CHECKPOINT".equals(traceCheckpoint)) {
            RunCommandTrace.record(context, traceCheckpoint, "BridgeReceiver", runId,
                    RunCommandTrace.bridgeMessage(intent),
                    DebugRecorder.details("action", action, "sequence", intent.getLongExtra("sequence", 0L)));
        }
        HudUpdate update = HudUpdate.fromIntent(intent);
        RunStore.update(context, update);
        controller.post(update);
        OverlayHudService.publish(context, update);
        maybeToastApplyCompletion(context, update);
    }

    private static void maybeToastApplyCompletion(Context context, HudUpdate update) {
        if (!update.isTerminal() || !update.isApplyOperation()) return;
        String key = update.runId + ":" + update.status + ":" + update.sequence;
        if (context.getSharedPreferences(TOAST_PREFS, Context.MODE_PRIVATE).getBoolean(key, false)) return;
        context.getSharedPreferences(TOAST_PREFS, Context.MODE_PRIVATE)
                .edit()
                .putBoolean(key, true)
                .apply();
        showToast(context, update.applyToastText());
    }

    private static void showToast(Context context, String text) {
        String clean = text == null ? "" : text.trim();
        if (clean.isEmpty()) return;
        if (clean.length() > MAX_TOAST_LENGTH) clean = clean.substring(0, MAX_TOAST_LENGTH);
        Toast.makeText(context.getApplicationContext(), clean, Toast.LENGTH_SHORT).show();
    }

    private static boolean isExpectedBridgeAction(Context context, String action) {
        String prefix = context.getPackageName() + ".";
        return (prefix + "UPDATE").equals(action)
                || (prefix + "COMPLETE").equals(action)
                || (prefix + "FAIL").equals(action)
                || (prefix + "CANCEL").equals(action)
                || (prefix + "TOAST").equals(action);
    }
}
