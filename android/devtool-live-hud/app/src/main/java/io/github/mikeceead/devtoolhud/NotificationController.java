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
import android.provider.Settings;

import java.util.ArrayList;
import java.util.List;

final class NotificationController {
    static final String CHANNEL_ID = "devtool_live_runs_v1";
    private static final String GROUP_KEY = "devtool_live_runs";
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
                .setGroup(GROUP_KEY)
                .setOngoing(!update.isTerminal())
                .setAutoCancel(update.isTerminal())
                .setOnlyAlertOnce(!update.isTerminal())
                .setShowWhen(true)
                .setWhen(update.startedAtMs)
                .setUsesChronometer(!update.isTerminal())
                .setColor(statusColor(update))
                .setColorized(false)
                .setContentIntent(openAppIntent(update.runId))
                .addAction(new Notification.Action.Builder(
                        Icon.createWithResource(context, R.drawable.ic_done),
                        "Dismiss", dismissIntent(update.runId)).build());

        if (update.overlayRequested && !update.isTerminal()) {
            builder.addAction(new Notification.Action.Builder(
                    Icon.createWithResource(context, R.drawable.ic_start),
                    "Show HUD", showOverlayIntent(update.runId)).build());
        }
        if (!update.isTerminal()) requestPromotedOngoing(builder);

        if (Build.VERSION.SDK_INT >= 36) {
            applyProgressStyle(builder, update);
        } else {
            builder.setProgress(100, update.progress, update.indeterminate && !update.isTerminal());
        }

        if (Build.VERSION.SDK_INT >= 36) builder.setShortCriticalText(update.criticalText());
        Notification notification = builder.build();
        manager.notify(notificationTag(update.runId), NOTIFICATION_ID, notification);
    }

    void cancel(String runId) {
        manager.cancel(notificationTag(runId), NOTIFICATION_ID);
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

    private PendingIntent openAppIntent(String runId) {
        Intent intent = new Intent(context, MainActivity.class)
                .putExtra("run_id", runId)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        return PendingIntent.getActivity(context, runId.hashCode(), intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }


    private PendingIntent showOverlayIntent(String runId) {
        Intent intent = new Intent(context, ActionReceiver.class)
                .setAction(ActionReceiver.ACTION_START_OVERLAY)
                .putExtra("run_id", runId);
        return PendingIntent.getBroadcast(context, runId.hashCode() ^ 0x31a9, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private void requestPromotedOngoing(Notification.Builder builder) {
        if (Build.VERSION.SDK_INT < 36) return;
        try {
            Notification.Builder.class
                    .getMethod("setRequestPromotedOngoing", boolean.class)
                    .invoke(builder, true);
        } catch (ReflectiveOperationException ignored) {
            // API 36.0 exposes Live Updates before the typed 36.1 setter.
            builder.getExtras().putBoolean("android.requestPromotedOngoing", true);
        }
    }

    private PendingIntent dismissIntent(String runId) {
        Intent intent = new Intent(context, ActionReceiver.class)
                .setAction(ActionReceiver.ACTION_DISMISS)
                .putExtra("run_id", runId);
        return PendingIntent.getBroadcast(context, runId.hashCode() ^ 0x5a17, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static String notificationTag(String runId) {
        return "devtool:" + runId;
    }

    private int statusColor(HudUpdate update) {
        AppPalette palette = AppPalette.resolve(context);
        if (update.succeeded()) return palette.green;
        if (update.failedState()) return palette.red;
        if (update.status.equals("interrupted") || update.status.equals("cancelled")) return palette.orange;
        if (update.status.equals("waiting")) return palette.muted;
        return palette.yellow;
    }

    @android.annotation.TargetApi(36)
    private void applyProgressStyle(Notification.Builder builder, HudUpdate update) {
        Notification.ProgressStyle style = new Notification.ProgressStyle()
                .setStyledByProgress(true)
                .setProgressTrackerIcon(Icon.createWithResource(context, R.drawable.ic_tracker))
                .setProgressStartIcon(Icon.createWithResource(context, R.drawable.ic_start))
                .setProgressEndIcon(Icon.createWithResource(context, R.drawable.ic_done));

        if (update.isTerminal()) {
            style.setProgress(100)
                    .setProgressSegments(List.of(
                            new Notification.ProgressStyle.Segment(100).setColor(statusColor(update))));
        } else if (update.indeterminate) {
            style.setProgressIndeterminate(true)
                    .setProgressSegments(List.of(
                            new Notification.ProgressStyle.Segment(100).setColor(statusColor(update))));
        } else {
            List<Notification.ProgressStyle.Segment> segments = new ArrayList<>();
            segments.add(new Notification.ProgressStyle.Segment(15).setColor(AppPalette.resolve(context).cyan));  // restore
            segments.add(new Notification.ProgressStyle.Segment(45).setColor(AppPalette.resolve(context).yellow)); // build
            segments.add(new Notification.ProgressStyle.Segment(35).setColor(AppPalette.resolve(context).cyan)); // test
            segments.add(new Notification.ProgressStyle.Segment(5).setColor(AppPalette.resolve(context).green));   // commit
            style.setProgress(update.progress)
                    .setProgressSegments(segments)
                    .setProgressPoints(List.of(
                            new Notification.ProgressStyle.Point(15).setColor(Color.WHITE),
                            new Notification.ProgressStyle.Point(60).setColor(Color.WHITE),
                            new Notification.ProgressStyle.Point(95).setColor(Color.WHITE)));
        }
        builder.setStyle(style);
    }
}
