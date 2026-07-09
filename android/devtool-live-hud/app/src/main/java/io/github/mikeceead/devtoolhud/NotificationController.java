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
    private static final String PROMOTED_EXTRA = "android.requestPromotedOngoing";

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

        if (!update.isTerminal()) {
            // Literal key keeps the project buildable with API 36 while also
            // working on Android releases where the public constant arrived later.
            builder.getExtras().putBoolean(PROMOTED_EXTRA, true);
        }

        if (Build.VERSION.SDK_INT >= 36) {
            applyProgressStyle(builder, update);
        } else {
            builder.setProgress(100, update.progress, update.indeterminate && !update.isTerminal());
        }

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

    private static int statusColor(HudUpdate update) {
        if (update.succeeded()) return Color.rgb(94, 228, 155);
        if (update.failedState()) return Color.rgb(255, 105, 120);
        if (update.status.equals("interrupted") || update.status.equals("cancelled")) return Color.rgb(255, 176, 92);
        if (update.status.equals("waiting")) return Color.rgb(142, 151, 165);
        return Color.rgb(255, 209, 102);
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
            segments.add(new Notification.ProgressStyle.Segment(15).setColor(Color.rgb(86, 156, 214)));  // restore
            segments.add(new Notification.ProgressStyle.Segment(45).setColor(Color.rgb(255, 209, 102))); // build
            segments.add(new Notification.ProgressStyle.Segment(35).setColor(Color.rgb(184, 134, 255))); // test
            segments.add(new Notification.ProgressStyle.Segment(5).setColor(Color.rgb(94, 228, 155)));   // commit
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
