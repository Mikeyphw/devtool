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

import java.util.Locale;

/**
 * Phase 46 debug notifications.
 *
 * These notifications are deliberately safe action surfaces. They can open the
 * right diagnostic screen, copy bounded/redacted evidence, start the read-only
 * bridge probe, or copy the support bundle. They never run Git repair commands,
 * reset repositories, delete files, or mutate a worktree from the shade.
 */
final class DebugNotificationController {
    static final String CHANNEL_ID = "devtool_debug_notifications";
    static final String GROUP_KEY = "io.github.mikeceead.devtoolhud.DEBUG_NOTIFICATIONS";
    static final String EXTRA_EVENT_ID = "debug_event_id";
    static final String EXTRA_FINGERPRINT = "debug_fingerprint";
    static final String EXTRA_ISSUE_KEY = "debug_issue_key";

    private static final int SUMMARY_NOTIFICATION_ID = 0x46D00000;
    private static final int MAX_DETAIL = 900;
    private static final String SAFE_ACTION_LABELS = "Open Debug, Open Doctor, Open Validation, Copy Report, Copy Trace, Export Bundle, Run Probe";

    private DebugNotificationController() {}

    static void maybePost(Context context, DebugEventRecord event) {
        if (context == null || event == null || !event.problem()) return;
        Context app = context.getApplicationContext();
        NotificationManager manager = app.getSystemService(NotificationManager.class);
        if (manager == null || !notificationsAllowed(app)) return;
        ensureChannel(app, manager);

        String issueKey = issueKey(event);
        DebugNotice notice = noticeFor(event, issueKey);
        PendingIntent open = openIntent(app, event, notice.actionTarget);
        Notification.Builder builder = newBuilder(app, manager)
                .setSmallIcon(iconFor(issueKey))
                .setContentTitle(notice.title)
                .setContentText(notice.lead)
                .setStyle(new Notification.BigTextStyle().bigText(notice.bigText(event)))
                .setContentIntent(open)
                .setAutoCancel(true)
                .setOnlyAlertOnce(true)
                .setShowWhen(true)
                .setWhen(event.timestampMs > 0L ? event.timestampMs : System.currentTimeMillis())
                .setCategory(Notification.CATEGORY_STATUS)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setGroup(GROUP_KEY)
                .setColor(accentFor(app, issueKey, event.severity));
        builder.addAction(actionIcon(issueKey), notice.primaryAction, open);
        builder.addAction(R.drawable.ic_status, "Copy report", copyReportIntent(app, event, issueKey, notice));
        PendingIntent third = thirdActionIntent(app, event, issueKey);
        if (third != null) builder.addAction(thirdActionIcon(issueKey), thirdActionLabel(issueKey), third);
        if (Build.VERSION.SDK_INT >= 36) builder.setShortCriticalText(shortCriticalText(notice.title));
        builder.getExtras().putString("devtool.debug.issue_key", issueKey);
        builder.getExtras().putString("devtool.debug.event_type", event.eventType);
        builder.getExtras().putString("devtool.debug.safe_actions", SAFE_ACTION_LABELS);
        manager.notify(notificationId(event), builder.build());
        refreshSummary(app, manager);
    }

    static void postSupportBundleReady(Context context, String mode, String bundle) {
        if (context == null) return;
        Context app = context.getApplicationContext();
        NotificationManager manager = app.getSystemService(NotificationManager.class);
        if (manager == null || !notificationsAllowed(app)) return;
        ensureChannel(app, manager);
        String cleanMode = mode == null || mode.trim().isEmpty() ? "ready" : mode.trim();
        String summary = "Support bundle " + cleanMode + "\n"
                + "Safe actions: open Debug Workbench, copy bundle, copy health report.\n"
                + "Bundle bytes: " + (bundle == null ? 0 : bundle.length());
        Notification.Builder builder = newBuilder(app, manager)
                .setSmallIcon(R.drawable.ic_hud)
                .setContentTitle("Debug bundle ready")
                .setContentText("Redacted Support Bundle v2 is ready")
                .setStyle(new Notification.BigTextStyle().bigText(summary))
                .setContentIntent(openDebugIntent(app, "support_bundle"))
                .setAutoCancel(true)
                .setOnlyAlertOnce(true)
                .setShowWhen(true)
                .setWhen(System.currentTimeMillis())
                .setCategory(Notification.CATEGORY_STATUS)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setGroup(GROUP_KEY)
                .setColor(app.getColor(R.color.notification_success));
        builder.addAction(R.drawable.ic_hud, "Open Debug", openDebugIntent(app, "support_bundle"));
        builder.addAction(R.drawable.ic_status, "Copy bundle", copySupportBundleIntent(app));
        builder.addAction(R.drawable.ic_validate, "Copy health", copyHealthIntent(app));
        builder.getExtras().putString("devtool.debug.issue_key", "support_bundle_ready");
        builder.getExtras().putString("devtool.debug.safe_actions", SAFE_ACTION_LABELS);
        manager.notify(SUMMARY_NOTIFICATION_ID ^ 0x45B1, builder.build());
        refreshSummary(app, manager);
    }

    static String issueKey(DebugEventRecord event) {
        String joined = safe(event.eventType) + " " + safe(event.message) + " " + safe(event.detailJson);
        String classified = DebugIssueClassifier.classifyMessage(joined);
        String lower = joined.toLowerCase(Locale.ROOT);
        if (lower.contains("bridge_roundtrip_failed")) return "bridge_roundtrip_failed";
        if (lower.contains("termux_intent_sent") && !lower.contains("termux_wrapper_started")) return "devtool_launch_stalled";
        if (lower.contains("termux_wrapper_never_started") || lower.contains("wrapper never started")) return "devtool_launch_stalled";
        if (lower.contains("notification") && (lower.contains("blocked") || lower.contains("permission"))) return "notification_blocked";
        if (lower.contains("validation") && (lower.contains("failed") || lower.contains("blocked") || lower.contains("stopped"))) return "validation_stopped";
        if (safe(event.category).equals("repo") || safe(event.category).equals("repository") || !safe(event.repositoryId).isEmpty()
                || lower.contains("repo_") || lower.contains("git_") || lower.contains("runuser")) {
            if (lower.contains("orphan") || lower.contains("stale")) return "repo_operation_orphaned";
            return "repo_manager_needs_attention";
        }
        if (!classified.equals("generic_event")) return classified;
        return safe(event.category).isEmpty() ? "debug_event_problem" : safe(event.category) + "_debug_problem";
    }

    static String copyText(DebugEventRecord event, String issueKey, DebugNotice notice) {
        return "Devtool debug notification\n"
                + "issue_key=" + issueKey + '\n'
                + "severity=" + safe(event.severity) + '\n'
                + "category=" + safe(event.category) + '\n'
                + "event_type=" + safe(event.eventType) + '\n'
                + "run_id=" + safe(event.runId) + '\n'
                + "repository_id=" + safe(event.repositoryId) + '\n'
                + "operation_id=" + safe(event.operationId) + '\n'
                + "validation_session_id=" + safe(event.validationSessionId) + '\n'
                + "what_happened=" + notice.lead + '\n'
                + "safe_next_action=" + notice.safeNextAction + '\n'
                + "evidence=" + evidence(event) + '\n'
                + "safe_actions_only=true\n"
                + "safe_action_labels=" + SAFE_ACTION_LABELS + '\n';
    }

    private static Notification.Builder newBuilder(Context context, NotificationManager manager) {
        return new Notification.Builder(context, CHANNEL_ID);
    }

    private static boolean notificationsAllowed(Context context) {
        return Build.VERSION.SDK_INT < 33
                || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED;
    }

    private static void ensureChannel(Context context, NotificationManager manager) {
        NotificationChannel channel = new NotificationChannel(CHANNEL_ID, "Debug notifications",
                NotificationManager.IMPORTANCE_DEFAULT);
        channel.setDescription("Safe diagnostic notifications for Devtool debug events, probes, repositories and support bundles.");
        channel.enableVibration(false);
        channel.setShowBadge(false);
        manager.createNotificationChannel(channel);
    }

    private static void refreshSummary(Context context, NotificationManager manager) {
        Notification.InboxStyle style = new Notification.InboxStyle()
                .setBigContentTitle("Devtool debug notifications")
                .setSummaryText("Safe actions only");
        int count = 0;
        for (DebugEventRecord event : DevtoolDatabase.get(context).recentDebugEvents(8)) {
            if (!event.problem()) continue;
            style.addLine(event.severity + " · " + event.category + " · " + event.eventType);
            count++;
            if (count >= 5) break;
        }
        if (count == 0) return;
        Notification.Builder builder = newBuilder(context, manager)
                .setSmallIcon(R.drawable.ic_devtool_notification)
                .setContentTitle(count + " recent debug issue" + (count == 1 ? "" : "s"))
                .setContentText("Open Debug Workbench for guided fixes")
                .setStyle(style)
                .setContentIntent(openDebugIntent(context, "summary"))
                .setAutoCancel(true)
                .setOnlyAlertOnce(true)
                .setShowWhen(false)
                .setCategory(Notification.CATEGORY_STATUS)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setGroup(GROUP_KEY)
                .setGroupSummary(true)
                .setColor(context.getColor(R.color.notification_warning));
        manager.notify(SUMMARY_NOTIFICATION_ID, builder.build());
    }

    private static DebugNotice noticeFor(DebugEventRecord event, String issueKey) {
        String lower = (issueKey + " " + event.eventType + " " + event.message + " " + event.detailJson).toLowerCase(Locale.ROOT);
        if (issueKey.equals("devtool_launch_stalled") || lower.contains("wrapper never started")) {
            return new DebugNotice("Devtool launch stalled", "Termux shell did not confirm startup",
                    "Open Debug Workbench and run the bridge roundtrip probe.", "bridge_probe");
        }
        if (issueKey.equals("bridge_roundtrip_failed") || lower.contains("bridge_roundtrip_failed")) {
            return new DebugNotice("Bridge roundtrip failed", "Android → Termux → Android callback chain failed",
                    "Run the bridge probe again after checking Termux permission and token markers.", "bridge_probe");
        }
        if (issueKey.equals("repo_operation_orphaned")) {
            return new DebugNotice("Repo operation orphaned", "A repository operation appears stale or orphaned",
                    "Open the repository doctor and use confirmed recovery controls from there.", "repository");
        }
        if (issueKey.equals("repo_manager_needs_attention") || lower.contains("repo") || lower.contains("runuser")) {
            return new DebugNotice("Repo manager needs attention", "Repository execution or path diagnostics recorded a problem",
                    "Open the repository doctor/path resolver before retrying Git.", "repository");
        }
        if (issueKey.equals("validation_stopped") || lower.contains("validation")) {
            return new DebugNotice("Validation stopped", "Validation recorded a blocked, failed, or stale state",
                    "Open Validation Workbench and use current revision controls.", "validation");
        }
        if (issueKey.equals("notification_blocked")) {
            return new DebugNotice("Notification blocked", "Android notification permission or channel state needs review",
                    "Open app notification settings and allow Devtool notifications.", "notification_settings");
        }
        return new DebugNotice("Debug issue recorded", titleCase(event.category) + " recorded a diagnostic problem",
                "Open Debug Workbench and copy the bounded report if needed.", "debug");
    }

    private static PendingIntent openIntent(Context context, DebugEventRecord event, String target) {
        if ("bridge_probe".equals(target) || "debug".equals(target)) return openDebugIntent(context, target);
        if ("validation".equals(target)) return openValidationIntent(context, event.validationSessionId);
        if ("repository".equals(target)) return openRepositoryIntent(context, event.repositoryId);
        if ("notification_settings".equals(target)) return notificationSettingsIntent(context);
        if (!safe(event.runId).isEmpty()) return openRunIntent(context, event.runId);
        return openDebugIntent(context, target);
    }

    private static PendingIntent openDebugIntent(Context context, String reason) {
        Intent intent = new Intent(context, DebugWorkbenchActivity.class)
                .putExtra("debug_notification_reason", reason)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(context, ("debug:" + reason).hashCode() ^ 0x46D1, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static PendingIntent openRunIntent(Context context, String runId) {
        Intent intent = new Intent(context, LogViewerActivity.class)
                .putExtra(LogViewerActivity.EXTRA_RUN_ID, safe(runId))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(context, safe(runId).hashCode() ^ 0x46A2, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static PendingIntent openRepositoryIntent(Context context, String repositoryId) {
        Intent intent;
        if (safe(repositoryId).isEmpty()) {
            intent = new Intent(context, MainActivity.class).putExtra("open_page", 3);
        } else {
            intent = new Intent(context, RepositoryDetailActivity.class)
                    .putExtra(RepositoryDetailActivity.EXTRA_REPOSITORY_ID, repositoryId)
                    .putExtra(RepositoryDetailActivity.EXTRA_TAB, 4);
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(context, safe(repositoryId).hashCode() ^ 0x46A3, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static PendingIntent openValidationIntent(Context context, String sessionId) {
        Intent intent = new Intent(context, MainActivity.class)
                .putExtra("open_page", 1)
                .putExtra("run_section", "validation")
                .putExtra(ValidationStore.EXTRA_SESSION_ID, safe(sessionId))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(context, safe(sessionId).hashCode() ^ 0x46A4, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static PendingIntent notificationSettingsIntent(Context context) {
        Intent intent = new Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, context.getPackageName())
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(context, 0x46A5, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static PendingIntent copyReportIntent(Context context, DebugEventRecord event, String issueKey, DebugNotice notice) {
        Intent intent = new Intent(context, ActionReceiver.class)
                .setAction(ActionReceiver.ACTION_COPY_DEBUG_REPORT)
                .putExtra(EXTRA_EVENT_ID, event.eventId)
                .putExtra(EXTRA_FINGERPRINT, event.fingerprint)
                .putExtra(EXTRA_ISSUE_KEY, issueKey)
                .putExtra("summary", copyText(event, issueKey, notice));
        return PendingIntent.getBroadcast(context, (issueKey + event.eventId).hashCode() ^ 0x46B1, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static PendingIntent copyHealthIntent(Context context) {
        Intent intent = new Intent(context, ActionReceiver.class).setAction(ActionReceiver.ACTION_COPY_DEBUG_HEALTH);
        return PendingIntent.getBroadcast(context, 0x46B2, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static PendingIntent copySupportBundleIntent(Context context) {
        Intent intent = new Intent(context, ActionReceiver.class).setAction(ActionReceiver.ACTION_COPY_SUPPORT_BUNDLE);
        return PendingIntent.getBroadcast(context, 0x46B3, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static PendingIntent thirdActionIntent(Context context, DebugEventRecord event, String issueKey) {
        if (issueKey.equals("devtool_launch_stalled") || issueKey.equals("bridge_roundtrip_failed")) {
            Intent intent = new Intent(context, ActionReceiver.class).setAction(ActionReceiver.ACTION_RUN_BRIDGE_PROBE);
            return PendingIntent.getBroadcast(context, 0x46B4, intent,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        }
        if (issueKey.equals("repo_manager_needs_attention") || issueKey.equals("repo_operation_orphaned")) {
            return openRepositoryIntent(context, event.repositoryId);
        }
        if (issueKey.equals("validation_stopped")) return openValidationIntent(context, event.validationSessionId);
        if (issueKey.equals("notification_blocked")) return notificationSettingsIntent(context);
        if (!safe(event.runId).isEmpty()) return openRunIntent(context, event.runId);
        return copyHealthIntent(context);
    }

    private static String thirdActionLabel(String issueKey) {
        if (issueKey.equals("devtool_launch_stalled") || issueKey.equals("bridge_roundtrip_failed")) return "Run probe";
        if (issueKey.equals("repo_manager_needs_attention") || issueKey.equals("repo_operation_orphaned")) return "Open Doctor";
        if (issueKey.equals("validation_stopped")) return "Open Validation";
        if (issueKey.equals("notification_blocked")) return "Open settings";
        return "Copy health";
    }

    private static int thirdActionIcon(String issueKey) {
        if (issueKey.equals("repo_manager_needs_attention") || issueKey.equals("repo_operation_orphaned")) return R.drawable.ic_repository;
        if (issueKey.equals("validation_stopped")) return R.drawable.ic_validate;
        if (issueKey.equals("notification_blocked")) return R.drawable.ic_notification_status;
        if (issueKey.equals("devtool_launch_stalled") || issueKey.equals("bridge_roundtrip_failed")) return R.drawable.ic_link;
        return R.drawable.ic_status;
    }

    private static int actionIcon(String issueKey) { return thirdActionIcon(issueKey); }

    private static int iconFor(String issueKey) {
        if (issueKey.equals("repo_manager_needs_attention") || issueKey.equals("repo_operation_orphaned")) return R.drawable.ic_repository;
        if (issueKey.equals("validation_stopped")) return R.drawable.ic_validate;
        if (issueKey.equals("notification_blocked")) return R.drawable.ic_notification_status;
        if (issueKey.equals("devtool_launch_stalled") || issueKey.equals("bridge_roundtrip_failed")) return R.drawable.ic_terminal;
        return R.drawable.ic_devtool_notification;
    }

    private static int accentFor(Context context, String issueKey, String severity) {
        String clean = safe(severity).toLowerCase(Locale.ROOT);
        if (clean.equals("fatal") || clean.equals("error")) return context.getColor(R.color.notification_error);
        if (clean.equals("warning") || clean.equals("warn")) return context.getColor(R.color.notification_warning);
        if (issueKey.equals("support_bundle_ready")) return context.getColor(R.color.notification_success);
        return context.getColor(R.color.notification_running);
    }

    private static int notificationId(DebugEventRecord event) {
        String key = !safe(event.fingerprint).isEmpty() ? event.fingerprint : event.eventType + event.message;
        return 0x46D10000 ^ key.hashCode();
    }

    private static String evidence(DebugEventRecord event) {
        String detail = safe(event.detailJson);
        String message = safe(event.message);
        String evidence = message + (detail.isEmpty() || detail.equals("{}") ? "" : " · " + detail);
        evidence = SupportRedactor.redact(evidence);
        return evidence.length() <= MAX_DETAIL ? evidence : evidence.substring(0, MAX_DETAIL - 1) + "…";
    }

    private static String shortCriticalText(String value) {
        String clean = safe(value);
        return clean.length() <= 7 ? clean : clean.substring(0, 7);
    }

    private static String titleCase(String value) {
        String clean = safe(value).replace('_', ' ');
        if (clean.isEmpty()) return "App";
        return clean.substring(0, 1).toUpperCase(Locale.ROOT) + clean.substring(1);
    }

    private static String safe(String value) { return value == null ? "" : value.trim(); }

    static final class DebugNotice {
        final String title;
        final String lead;
        final String safeNextAction;
        final String actionTarget;
        final String primaryAction;

        DebugNotice(String title, String lead, String safeNextAction, String actionTarget) {
            this.title = title;
            this.lead = lead;
            this.safeNextAction = safeNextAction;
            this.actionTarget = actionTarget;
            this.primaryAction = actionTarget.equals("repository") ? "Open Doctor"
                    : actionTarget.equals("validation") ? "Open Validation"
                    : actionTarget.equals("notification_settings") ? "Open settings" : "Open Debug";
        }

        String bigText(DebugEventRecord event) {
            return lead + "\n\nEvidence:\n" + evidence(event)
                    + "\n\nSafe next action:\n" + safeNextAction
                    + "\n\nSafe actions only: open, copy, probe, or export diagnostics.";
        }
    }
}
