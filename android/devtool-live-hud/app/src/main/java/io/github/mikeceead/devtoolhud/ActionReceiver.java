package io.github.mikeceead.devtoolhud;

import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.widget.Toast;

public final class ActionReceiver extends BroadcastReceiver {
    static final String ACTION_DISMISS = "io.github.mikeceead.devtoolhud.DISMISS";
    static final String ACTION_START_OVERLAY = "io.github.mikeceead.devtoolhud.START_OVERLAY";
    static final String ACTION_STOP_OVERLAY = "io.github.mikeceead.devtoolhud.STOP_OVERLAY";
    static final String ACTION_COPY_SUMMARY = "io.github.mikeceead.devtoolhud.COPY_SUMMARY";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        if (ACTION_DISMISS.equals(action)) {
            String runId = intent.getStringExtra("run_id");
            new NotificationController(context).cancel(runId);
            OverlayHudService.cancel(runId);
            return;
        }
        if (ACTION_COPY_SUMMARY.equals(action)) {
            String summary = intent.getStringExtra("summary");
            if (summary == null || summary.trim().isEmpty()) {
                RunRecord record = RunStore.find(context, intent.getStringExtra("run_id"));
                if (record != null) summary = record.summary;
            }
            if (summary == null || summary.trim().isEmpty()) {
                Toast.makeText(context, "No summary is available yet", Toast.LENGTH_SHORT).show();
                return;
            }
            try {
                ClipboardManager clipboard = context.getSystemService(ClipboardManager.class);
                if (clipboard == null) throw new IllegalStateException("clipboard unavailable");
                clipboard.setPrimaryClip(ClipData.newPlainText("Devtool summary", summary));
                Toast.makeText(context, "Summary copied", Toast.LENGTH_SHORT).show();
            } catch (RuntimeException error) {
                Toast.makeText(context, "Could not copy the summary", Toast.LENGTH_LONG).show();
            }
            return;
        }
        if (ACTION_START_OVERLAY.equals(action)) {
            OverlayHudService.setEnabled(context, true);
            return;
        }
        if (ACTION_STOP_OVERLAY.equals(action)) {
            OverlayHudService.setEnabled(context, false);
        }
    }
}
