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
    static final String ACTION_VALIDATION_PAUSE = "io.github.mikeceead.devtoolhud.VALIDATION_PAUSE";
    static final String ACTION_VALIDATION_STOP = "io.github.mikeceead.devtoolhud.VALIDATION_STOP";
    static final String ACTION_VALIDATION_CANCEL_PAUSE = "io.github.mikeceead.devtoolhud.VALIDATION_CANCEL_PAUSE";
    static final String ACTION_VALIDATION_RESUME = "io.github.mikeceead.devtoolhud.VALIDATION_RESUME";
    static final String ACTION_VALIDATION_RETRY = "io.github.mikeceead.devtoolhud.VALIDATION_RETRY";
    static final String ACTION_VALIDATION_RETRY_SAFE = "io.github.mikeceead.devtoolhud.VALIDATION_RETRY_SAFE";
    static final String ACTION_VALIDATION_RECOVER = "io.github.mikeceead.devtoolhud.VALIDATION_RECOVER";
    static final String ACTION_RUN_BRIDGE_PROBE = "io.github.mikeceead.devtoolhud.RUN_BRIDGE_PROBE";
    static final String ACTION_COPY_DEBUG_REPORT = "io.github.mikeceead.devtoolhud.COPY_DEBUG_REPORT";
    static final String ACTION_COPY_DEBUG_HEALTH = "io.github.mikeceead.devtoolhud.COPY_DEBUG_HEALTH";
    static final String ACTION_COPY_SUPPORT_BUNDLE = "io.github.mikeceead.devtoolhud.COPY_SUPPORT_BUNDLE";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        if (ACTION_VALIDATION_PAUSE.equals(action) || ACTION_VALIDATION_STOP.equals(action)
                || ACTION_VALIDATION_CANCEL_PAUSE.equals(action) || ACTION_VALIDATION_RESUME.equals(action) || ACTION_VALIDATION_RETRY.equals(action)
                || ACTION_VALIDATION_RETRY_SAFE.equals(action) || ACTION_VALIDATION_RECOVER.equals(action)) {
            String sessionId = intent.getStringExtra(ValidationStore.EXTRA_SESSION_ID);
            ValidationSessionRecord session = ValidationStore.find(context, sessionId);
            long expectedRevision = intent.getLongExtra("expected_revision", -1L);
            String expectedAttempt = intent.getStringExtra("expected_attempt_id");
            String expectedRepository = intent.getStringExtra("repository_id");
            if (session == null) {
                Toast.makeText(context, "Validation session is no longer available", Toast.LENGTH_LONG).show();
                return;
            }
            ValidationPhaseRecord active = session.activePhase();
            boolean stale = expectedRevision >= 0 && expectedRevision != session.revision;
            if (!stale && expectedRepository != null && !expectedRepository.trim().isEmpty()) {
                stale = !session.identityMatches(expectedRepository);
            }
            if (!stale && expectedAttempt != null && !expectedAttempt.isEmpty()) {
                stale = active == null || !expectedAttempt.equals(active.currentAttemptId);
            }
            if (stale) {
                Toast.makeText(context, "Validation has already advanced; opening current state", Toast.LENGTH_LONG).show();
                openValidation(context, session.sessionId);
                return;
            }
            TermuxCommandRunner.LaunchResult result;
            if (ACTION_VALIDATION_PAUSE.equals(action)) result = ValidationController.pause(context, session);
            else if (ACTION_VALIDATION_CANCEL_PAUSE.equals(action)) result = ValidationController.cancelPause(context, session);
            else if (ACTION_VALIDATION_STOP.equals(action)) result = ValidationController.stop(context, session);
            else if (ACTION_VALIDATION_RETRY_SAFE.equals(action)) result = ValidationController.retry(context, session, true);
            else if (ACTION_VALIDATION_RETRY.equals(action)) result = ValidationController.retry(context, session, false);
            else if (ACTION_VALIDATION_RECOVER.equals(action)) result = ValidationController.recover(context, session);
            else result = ValidationController.resume(context, session);
            ValidationController.showLaunchResult(context, result, "Validation action requested");
            return;
        }
        if (ACTION_RUN_BRIDGE_PROBE.equals(action)) {
            TermuxCommandRunner.LaunchResult result = BridgeRoundtripProbe.start(context);
            Toast.makeText(context, result.started ? "Bridge roundtrip probe started" : result.error,
                    result.started ? Toast.LENGTH_SHORT : Toast.LENGTH_LONG).show();
            return;
        }
        if (ACTION_COPY_DEBUG_REPORT.equals(action)) {
            String summary = intent.getStringExtra("summary");
            copyToClipboard(context, "Devtool debug report", summary,
                    summary == null || summary.trim().isEmpty() ? "No debug report is available" : "Debug report copied");
            return;
        }
        if (ACTION_COPY_DEBUG_HEALTH.equals(action)) {
            copyToClipboard(context, "Devtool debug health", DebugWorkbenchHealth.plainText(context), "Debug health copied");
            return;
        }
        if (ACTION_COPY_SUPPORT_BUNDLE.equals(action)) {
            copyToClipboard(context, "Devtool support bundle", SupportBundle.build(context), "Support bundle copied");
            return;
        }
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
            String toast = intent.getStringExtra("toast");
            copyToClipboard(context, "Devtool summary", summary,
                    toast == null || toast.trim().isEmpty() ? "Summary copied" : toast);
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
                RunStore.interrupted(context, runId, "Stopped tracking from the notification. Termux may still need manual cleanup.");
                Toast.makeText(context,
                        opened ? "Stopped tracking and opened Termux" : "Stopped tracking; Termux could not be opened",
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
    private static void copyToClipboard(Context context, String label, String value, String toast) {
        if (value == null || value.trim().isEmpty()) {
            Toast.makeText(context, toast == null || toast.trim().isEmpty() ? "Nothing to copy" : toast, Toast.LENGTH_LONG).show();
            return;
        }
        try {
            ClipboardManager clipboard = context.getSystemService(ClipboardManager.class);
            if (clipboard == null) throw new IllegalStateException("clipboard unavailable");
            clipboard.setPrimaryClip(ClipData.newPlainText(label, value));
            Toast.makeText(context, toast == null || toast.trim().isEmpty() ? "Copied" : toast, Toast.LENGTH_SHORT).show();
        } catch (RuntimeException error) {
            Toast.makeText(context, "Could not copy text", Toast.LENGTH_LONG).show();
        }
    }

    private static void openValidation(Context context, String sessionId) {
        Intent open = new Intent(context, MainActivity.class)
                .putExtra("open_page", 1)
                .putExtra("run_section", "validation")
                .putExtra(ValidationStore.EXTRA_SESSION_ID, sessionId)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        context.startActivity(open);
    }

}
