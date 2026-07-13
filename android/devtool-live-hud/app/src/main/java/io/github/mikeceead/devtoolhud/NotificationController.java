package io.github.mikeceead.devtoolhud;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.drawable.Icon;
import android.os.Build;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

final class NotificationController {
    static final String CHANNEL_ID = "devtool_live_runs_v1";
    private static final int NOTIFICATION_ID = 7101;

    private final Context context;
    private final NotificationManager manager;

    NotificationController(Context context) {
        this.context = context.getApplicationContext();
        this.manager = context.getSystemService(NotificationManager.class);
        ensureChannel();
    }

    void post(HudUpdate update) {
        Notification.Builder builder = new Notification.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_devtool_notification)
                .setContentTitle(update.title())
                .setContentText(update.contentText())
                .setSubText(update.subText())
                .setCategory(Notification.CATEGORY_PROGRESS)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setOngoing(!update.isTerminal())
                .setAutoCancel(update.isTerminal())
                .setOnlyAlertOnce(true)
                .setShowWhen(true)
                .setWhen(update.startedAtMs)
                .setUsesChronometer(!update.isTerminal())
                .setColor(statusColor(update))
                .setColorized(false)
                .setContentIntent(openLogIntent(update.runId));

        // Keep actions stable and limited to three. Tapping Open log always
        // navigates directly to this run's history entry.
        builder.addAction(new Notification.Action.Builder(
                Icon.createWithResource(context, R.drawable.ic_start),
                "Open log", openLogIntent(update.runId)).build());
        builder.addAction(new Notification.Action.Builder(
                Icon.createWithResource(context, R.drawable.ic_done),
                "Copy summary", copySummaryIntent(update)).build());

        if (update.isTerminal()) {
            builder.addAction(new Notification.Action.Builder(
                    Icon.createWithResource(context, R.drawable.ic_done),
                    "Dismiss", dismissIntent(update.runId)).build());
        } else if (update.overlayRequested) {
            builder.addAction(new Notification.Action.Builder(
                    Icon.createWithResource(context, R.drawable.ic_start),
                    "Show HUD", showOverlayIntent(update.runId)).build());
        }

        if (!update.isTerminal()) requestPromotedOngoing(builder);

        if (Build.VERSION.SDK_INT >= 36) {
            applyProgressStyle(builder, update);
            builder.setShortCriticalText(update.criticalText());
        } else {
            builder.setProgress(100, update.displayProgress(), update.indeterminate && !update.isTerminal());
            builder.setStyle(new Notification.BigTextStyle()
                    .bigText(update.expandedText())
                    .setBigContentTitle(update.title())
                    .setSummaryText(update.subText()));
        }

        manager.notify(NOTIFICATION_ID, builder.build());
    }

    void cancel(String runId) {
        manager.cancel(NOTIFICATION_ID);
    }

    private void ensureChannel() {
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                context.getString(R.string.channel_live_name),
                NotificationManager.IMPORTANCE_DEFAULT);
        channel.setDescription(context.getString(R.string.channel_live_description));
        channel.setSound(null, null);
        channel.enableVibration(false);
        channel.setShowBadge(false);
        manager.createNotificationChannel(channel);
    }

    private PendingIntent openLogIntent(String runId) {
        Intent intent = new Intent(context, MainActivity.class)
                .putExtra("run_id", runId)
                .putExtra("open_log", true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(context, runId.hashCode() ^ 0x1f31, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private PendingIntent showOverlayIntent(String runId) {
        Intent intent = new Intent(context, ActionReceiver.class)
                .setAction(ActionReceiver.ACTION_START_OVERLAY)
                .putExtra("run_id", runId);
        return PendingIntent.getBroadcast(context, runId.hashCode() ^ 0x31a9, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private PendingIntent copySummaryIntent(HudUpdate update) {
        Intent intent = new Intent(context, ActionReceiver.class)
                .setAction(ActionReceiver.ACTION_COPY_SUMMARY)
                .putExtra("run_id", update.runId)
                .putExtra("summary", update.copySummaryText());
        return PendingIntent.getBroadcast(context, update.runId.hashCode() ^ 0x441d, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private PendingIntent dismissIntent(String runId) {
        Intent intent = new Intent(context, ActionReceiver.class)
                .setAction(ActionReceiver.ACTION_DISMISS)
                .putExtra("run_id", runId);
        return PendingIntent.getBroadcast(context, runId.hashCode() ^ 0x5a17, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private void requestPromotedOngoing(Notification.Builder builder) {
        if (Build.VERSION.SDK_INT < 36) return;
        try {
            Notification.Builder.class
                    .getMethod("setRequestPromotedOngoing", boolean.class)
                    .invoke(builder, true);
        } catch (ReflectiveOperationException ignored) {
            builder.getExtras().putBoolean("android.requestPromotedOngoing", true);
        }
    }

    private int statusColor(HudUpdate update) {
        AppPalette palette = AppPalette.resolve(context);
        String phase = update.phase.toLowerCase(Locale.ROOT);
        if (phase.contains("rollback") || phase.contains("rolling back")) return palette.orange;
        if (update.succeeded()) return palette.green;
        if (update.failedState()) return palette.red;
        if (update.status.equals("interrupted") || update.status.equals("cancelled")) return palette.orange;
        if (update.status.equals("waiting")) return palette.muted;
        return palette.cyan;
    }

    @android.annotation.TargetApi(36)
    private void applyProgressStyle(Notification.Builder builder, HudUpdate update) {
        Notification.ProgressStyle style = new Notification.ProgressStyle()
                .setStyledByProgress(true)
                .setProgressTrackerIcon(Icon.createWithResource(context, R.drawable.ic_tracker))
                .setProgressStartIcon(Icon.createWithResource(context, R.drawable.ic_start))
                .setProgressEndIcon(Icon.createWithResource(context, R.drawable.ic_done));

        List<Notification.ProgressStyle.Segment> segments = new ArrayList<>();
        List<Notification.ProgressStyle.Point> points = new ArrayList<>();
        int count = Math.max(1, update.steps.size());
        int used = 0;
        AppPalette palette = AppPalette.resolve(context);
        for (int i = 0; i < update.steps.size(); i++) {
            HudUpdate.Step step = update.steps.get(i);
            int length = i == update.steps.size() - 1 ? 100 - used : Math.max(1, 100 / count);
            used += length;
            int color = step.failed() ? palette.red
                    : step.succeeded() ? palette.green
                    : step.running() ? palette.cyan
                    : palette.muted;
            segments.add(new Notification.ProgressStyle.Segment(length).setColor(color));
            if (i < update.steps.size() - 1) {
                points.add(new Notification.ProgressStyle.Point(used).setColor(Color.WHITE));
            }
        }
        if (segments.isEmpty()) {
            segments.add(new Notification.ProgressStyle.Segment(100).setColor(statusColor(update)));
        }
        style.setProgress(update.displayProgress()).setProgressSegments(segments);
        if (!points.isEmpty()) style.setProgressPoints(points);
        builder.setStyle(style);
    }
}
