package io.github.mikeceead.devtoolhud;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;

final class RepositoryNotificationController {
    private static final String CHANNEL = "devtool_repository_operations";
    private final Context context;
    private final NotificationManager manager;

    RepositoryNotificationController(Context context) {
        this.context = context.getApplicationContext();
        this.manager = this.context.getSystemService(NotificationManager.class);
        if (manager != null) {
            NotificationChannel channel = new NotificationChannel(CHANNEL, "Repository operations",
                    NotificationManager.IMPORTANCE_DEFAULT);
            channel.setDescription("Completion and failure results for Git operations started from Devtool.");
            manager.createNotificationChannel(channel);
        }
    }

    void show(RepositoryRecord repository, RepositoryOperationRecord operation) {
        if (manager == null || operation == null || operation.state.equals("queued") || operation.state.equals("running")) return;
        if (Build.VERSION.SDK_INT >= 33 && context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) return;
        String title = repository == null ? "Repository operation" : repository.name();
        String state = operation.state.equals("ok") ? "completed"
                : operation.state.equals("cancelled") ? "cancelled"
                : operation.state.equals("interrupted") ? "interrupted" : "failed";
        Intent open = new Intent(context, RepositoryDetailActivity.class)
                .putExtra(RepositoryDetailActivity.EXTRA_REPOSITORY_ID, operation.repositoryId)
                .putExtra(RepositoryDetailActivity.EXTRA_TAB, 4)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pending = PendingIntent.getActivity(context, operation.operationId.hashCode(), open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder builder = new Notification.Builder(context, CHANNEL);
        builder.setSmallIcon(R.drawable.ic_repository)
                .setContentTitle(title + " · " + operation.type.replace('_', ' ').toLowerCase(java.util.Locale.ROOT))
                .setContentText(operation.summary.isEmpty() ? "Git operation " + state : operation.summary)
                .setStyle(new Notification.BigTextStyle().bigText(operation.summary))
                .setContentIntent(pending)
                .setAutoCancel(true)
                .setCategory(Notification.CATEGORY_STATUS)
                .setOnlyAlertOnce(true);
        manager.notify(0x52A00000 ^ operation.operationId.hashCode(), builder.build());
    }
}
