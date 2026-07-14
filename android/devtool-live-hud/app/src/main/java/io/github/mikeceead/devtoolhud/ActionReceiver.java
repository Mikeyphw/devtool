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
    static final String ACTION_CANCEL_RUN = "io.github.mikeceead.devtoolhud.CANCEL_RUN";
    static final String ACTION_RETRY = "io.github.mikeceead.devtoolhud.RETRY";
    static final String ACTION_KEEP_VISIBLE = "io.github.mikeceead.devtoolhud.KEEP_VISIBLE";

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
                String toast = intent.getStringExtra("toast");
                Toast.makeText(context, toast == null || toast.trim().isEmpty() ? "Summary copied" : toast,
                        Toast.LENGTH_SHORT).show();
            } catch (RuntimeException error) {
                Toast.makeText(context, "Could not copy the summary", Toast.LENGTH_LONG).show();
            }
            return;
        }
        if (ACTION_CANCEL_RUN.equals(action)) {
            String runId = intent.getStringExtra("run_id");
            if (RootBridgeManager.cancel(context, runId)) {
                RunStore.cancelled(context, runId);
                new NotificationController(context).cancel(runId);
                OverlayHudService.cancel(runId);
                Toast.makeText(context, "Cancellation requested", Toast.LENGTH_SHORT).show();
            } else if (runId != null && runId.startsWith("termux-")) {
                boolean opened = TermuxCommandRunner.openTermux(context);
                Toast.makeText(context,
                        opened ? "Opened Termux — stop the active command there" : "This Termux run cannot be stopped remotely",
                        Toast.LENGTH_LONG).show();
            } else {
                Toast.makeText(context, "This run cannot be cancelled remotely", Toast.LENGTH_LONG).show();
            }
            return;
        }
        if (ACTION_RETRY.equals(action)) {
            String runId = intent.getStringExtra("run_id");
            RunRecord record = RunStore.find(context, runId);
            if (record == null || record.command == null || record.command.trim().isEmpty()) {
                Toast.makeText(context, "No saved command is available to retry", Toast.LENGTH_LONG).show();
                return;
            }
            boolean overlay = AppSettings.overlayByDefault(context);
            TermuxCommandRunner.LaunchResult result = runId != null && runId.startsWith("root-") && RootBridgeManager.isReady(context)
                    ? RootBridgeManager.run(context, record.command, record.workdir, overlay)
                    : TermuxCommandRunner.run(context, record.command, record.workdir,
                            !"foreground".equals(record.launchMode), overlay, null);
            Toast.makeText(context, result.started ? "Retry started" : "Retry failed: " + result.error,
                    result.started ? Toast.LENGTH_SHORT : Toast.LENGTH_LONG).show();
            return;
        }
        if (ACTION_KEEP_VISIBLE.equals(action)) {
            Toast.makeText(context, "Notification kept visible for recovery", Toast.LENGTH_SHORT).show();
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
