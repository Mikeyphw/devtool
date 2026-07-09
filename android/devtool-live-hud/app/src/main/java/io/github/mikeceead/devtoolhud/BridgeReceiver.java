package io.github.mikeceead.devtoolhud;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

public final class BridgeReceiver extends BroadcastReceiver {
    private static final String TAG = "DevtoolHudBridge";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) return;
        if (!BridgeSecrets.accepts(context, intent.getStringExtra("token"))) {
            Log.w(TAG, "Rejected update with an invalid bridge token");
            return;
        }

        NotificationController controller = new NotificationController(context);
        String action = intent.getAction() == null ? "" : intent.getAction();
        String runId = intent.getStringExtra("run_id");
        if (action.endsWith("CANCEL")) {
            controller.cancel(runId);
            OverlayHudService.cancel(runId);
            return;
        }

        HudUpdate update = HudUpdate.fromIntent(intent);
        RunStore.update(context, update);
        controller.post(update);
        OverlayHudService.publish(context, update);
    }
}
