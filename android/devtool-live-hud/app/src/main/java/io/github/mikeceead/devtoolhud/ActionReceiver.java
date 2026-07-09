package io.github.mikeceead.devtoolhud;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class ActionReceiver extends BroadcastReceiver {
    static final String ACTION_DISMISS = "io.github.mikeceead.devtoolhud.DISMISS";
    static final String ACTION_START_OVERLAY = "io.github.mikeceead.devtoolhud.START_OVERLAY";
    static final String ACTION_STOP_OVERLAY = "io.github.mikeceead.devtoolhud.STOP_OVERLAY";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) return;
        if (ACTION_DISMISS.equals(intent.getAction())) {
            String runId = intent.getStringExtra("run_id");
            new NotificationController(context).cancel(runId);
            OverlayHudService.cancel(runId);
            return;
        }
        if (ACTION_START_OVERLAY.equals(intent.getAction())) {
            // This user-tapped notification action is the reliable Android 15+
            // path for starting the foreground overlay service.
            OverlayHudService.setEnabled(context, true);
            return;
        }
        if (ACTION_STOP_OVERLAY.equals(intent.getAction())) {
            OverlayHudService.setEnabled(context, false);
        }
    }
}
