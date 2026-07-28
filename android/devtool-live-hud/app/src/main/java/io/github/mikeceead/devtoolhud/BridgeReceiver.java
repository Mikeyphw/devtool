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
        if (!BridgeSecrets.accepts(context, intent.getStringExtra("token"))) {
            Log.w(TAG, "Rejected update with an invalid bridge token");
            return;
        }

        String action = intent.getAction() == null ? "" : intent.getAction();
        if (!isExpectedBridgeAction(context, action)) {
            Log.w(TAG, "Rejected bridge update with unexpected action " + action);
            return;
        }
        String runId = intent.getStringExtra("run_id");
        if (action.endsWith("TOAST")) {
            showToast(context, intent.getStringExtra("toast"));
            return;
        }
        if (runId == null || runId.trim().isEmpty()) {
            Log.w(TAG, "Rejected bridge update without a run id");
            return;
        }
        if (!action.endsWith("CANCEL") && !BridgeUpdateGate.accept(context, intent)) {
            Log.d(TAG, "Ignored stale or out-of-order update for run " + runId);
            return;
        }
        NotificationController controller = new NotificationController(context);
        if (action.endsWith("CANCEL")) {
            RunStore.cancelled(context, runId);
            controller.cancel(runId);
            OverlayHudService.cancel(runId);
            return;
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
