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
            respond(BridgeProtocol.RESULT_TOKEN_REJECTED, "invalid-token");
            return;
        }

        int protocolVersion = intent.hasExtra("protocol_version")
                ? intent.getIntExtra("protocol_version", -1)
                : BridgeProtocol.VERSION;
        if (protocolVersion != BridgeProtocol.VERSION) {
            DebugRecorder.warning(context, "bridge", "BRIDGE_PROTOCOL_REJECTED", "BridgeReceiver",
                    runId, "", "", "", "Rejected unsupported bridge protocol",
                    DebugRecorder.details("action", action, "protocol_version", protocolVersion));
            Log.w(TAG, "Rejected unsupported bridge protocol " + protocolVersion);
            respond(BridgeProtocol.RESULT_PROTOCOL_REJECTED, "unsupported-protocol");
            return;
        }

        if (!BridgeProtocol.isExpectedAction(context.getPackageName(), action)) {
            DebugRecorder.warning(context, "bridge", "BRIDGE_ACTION_REJECTED", "BridgeReceiver",
                    runId, "", "", "", "Rejected bridge update with unexpected action",
                    DebugRecorder.details("action", action));
            Log.w(TAG, "Rejected bridge update with unexpected action " + action);
            respond(BridgeProtocol.RESULT_ACTION_REJECTED, "unknown-action");
            return;
        }
        if (action.endsWith("TOAST")) {
            showToast(context, intent.getStringExtra("toast"));
            respond(BridgeProtocol.RESULT_ACCEPTED, "accepted");
            return;
        }
        if (runId == null || runId.trim().isEmpty()) {
            DebugRecorder.warning(context, "bridge", "BRIDGE_RUN_ID_MISSING", "BridgeReceiver",
                    "", "", "", "", "Rejected bridge update without a run id",
                    DebugRecorder.details("action", action));
            Log.w(TAG, "Rejected bridge update without a run id");
            respond(BridgeProtocol.RESULT_RUN_ID_MISSING, "missing-run-id");
            return;
        }
        if (!action.endsWith("CANCEL") && !BridgeUpdateGate.accept(context, intent)) {
            DebugRecorder.info(context, "bridge", "BRIDGE_UPDATE_STALE", "BridgeReceiver",
                    runId, "", "", "", "Ignored stale or out-of-order update",
                    DebugRecorder.details("action", action, "sequence", intent.getLongExtra("sequence", 0L)));
            Log.d(TAG, "Ignored stale or out-of-order update for run " + runId);
            respond(BridgeProtocol.RESULT_IGNORED_STALE, "stale-update");
            return;
        }
        NotificationController controller = new NotificationController(context);
        if (action.endsWith("CANCEL")) {
            DebugRecorder.warning(context, "bridge", "BRIDGE_CANCEL_ACCEPTED", "BridgeReceiver",
                    runId, "", "", "", "Bridge cancellation accepted", DebugRecorder.details("action", action));
            RunStore.cancelled(context, runId);
            controller.cancel(runId);
            OverlayHudService.cancel(runId);
            respond(BridgeProtocol.RESULT_ACCEPTED, "accepted");
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
        respond(BridgeProtocol.RESULT_ACCEPTED, "accepted");
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

    private void respond(int resultCode, String resultData) {
        if (!isOrderedBroadcast()) return;
        setResultCode(resultCode);
        setResultData(resultData);
    }
}
