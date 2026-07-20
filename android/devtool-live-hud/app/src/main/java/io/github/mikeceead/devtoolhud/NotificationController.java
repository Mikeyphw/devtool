package io.github.mikeceead.devtoolhud;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.os.Build;
import android.view.View;
import android.widget.RemoteViews;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

final class NotificationController {
    static final String CHANNEL_ID = "devtool_live_runs_v3";
    static final String GROUP_KEY = "io.github.mikeceead.devtoolhud.LIVE_RUNS";
    private static final int SUMMARY_NOTIFICATION_ID = 7100;

    private static final int[] PHASE_ROWS = {
            R.id.notification_phase_row_1,
            R.id.notification_phase_row_2,
            R.id.notification_phase_row_3,
    };
    private static final int[] PHASE_MARKS = {
            R.id.notification_phase_mark_1,
            R.id.notification_phase_mark_2,
            R.id.notification_phase_mark_3,
    };
    private static final int[] PHASE_NAMES = {
            R.id.notification_phase_name_1,
            R.id.notification_phase_name_2,
            R.id.notification_phase_name_3,
    };
    private static final int[] PHASE_DETAILS = {
            R.id.notification_phase_detail_1,
            R.id.notification_phase_detail_2,
            R.id.notification_phase_detail_3,
    };
    private static final int[] ACTION_VIEWS = {
            R.id.notification_action_1,
            R.id.notification_action_2,
            R.id.notification_action_3,
    };

    private final Context context;
    private final NotificationManager manager;

    NotificationController(Context context) {
        this.context = context.getApplicationContext();
        this.manager = context.getSystemService(NotificationManager.class);
        ensureChannel();
    }

    void post(HudUpdate update) {
        boolean largeText = context.getResources().getConfiguration().fontScale >= 1.3f;
        RemoteViews compact = new RemoteViews(context.getPackageName(), R.layout.notification_devtool_compact);
        RemoteViews expanded = new RemoteViews(context.getPackageName(), largeText
                ? R.layout.notification_devtool_expanded_large_text
                : R.layout.notification_devtool_expanded);
        bindHeader(compact, update, false);
        bindHeader(expanded, update, true);
        bindExpanded(expanded, update, largeText);

        Notification.Builder builder = new Notification.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_devtool_notification)
                .setContentTitle(update.notificationTaskTitle())
                .setContentText(update.notificationCompactStatus())
                .setSubText(update.runnerLabel() + " · " + update.environmentLabel())
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
                .setGroup(GROUP_KEY)
                .setContentIntent(openRunIntent(update.runId))
                .setCustomContentView(compact)
                .setCustomBigContentView(expanded)
                .setCustomHeadsUpContentView(compact)
                .setStyle(new Notification.DecoratedCustomViewStyle())
                .setProgress(100, update.displayProgress(), update.indeterminate && !update.isTerminal());

        if (!update.isTerminal()) requestPromotedOngoing(builder);
        if (Build.VERSION.SDK_INT >= 36) builder.setShortCriticalText(update.criticalText());
        builder.getExtras().putString("devtool.expanded_text", update.expandedText());
        builder.getExtras().putString("devtool.run_id", update.runId);
        builder.getExtras().putString("devtool.project_type", update.projectType);
        builder.getExtras().putString("devtool.execution_environment", update.executionEnvironment);
        builder.getExtras().putString("devtool.notification_layout", "mock-v1");

        manager.notify(notificationId(update.runId), builder.build());
        refreshGroupSummary();
    }

    void cancel(String runId) {
        if (runId == null || runId.trim().isEmpty()) return;
        manager.cancel(notificationId(runId));
        refreshGroupSummary();
    }

    private void bindHeader(RemoteViews views, HudUpdate update, boolean expanded) {
        String mode = update.notificationMode();
        int accent = accentColor(mode);
        views.setInt(R.id.notification_root, "setBackgroundResource", cardDrawable(mode));
        views.setInt(R.id.notification_app_icon, "setBackgroundResource", badgeDrawable(mode));
        views.setInt(R.id.notification_app_icon, "setColorFilter", accent);
        views.setInt(R.id.notification_state_dot, "setColorFilter", accent);
        views.setTextColor(R.id.notification_state_label, accent);
        views.setTextColor(R.id.notification_progress_label, accent);
        views.setTextViewText(R.id.notification_state_label, update.notificationStateLabel());
        views.setTextViewText(R.id.notification_task_title, update.notificationTaskTitle());
        views.setTextViewText(R.id.notification_progress_label, update.notificationProgressLabel());
        views.setProgressBar(R.id.notification_progress, 100, update.displayProgress(), update.indeterminate && !update.isTerminal());
        views.setContentDescription(R.id.notification_root,
                update.notificationStateLabel() + ". " + update.notificationTaskTitle() + ". " + update.notificationCompactStatus());
        if (Build.VERSION.SDK_INT >= 31) {
            views.setColorStateList(R.id.notification_progress, "setProgressTintList", ColorStateList.valueOf(accent));
        }
        if (!expanded) views.setTextViewText(R.id.notification_compact_status, update.notificationCompactStatus());
    }

    private void bindExpanded(RemoteViews views, HudUpdate update, boolean largeText) {
        views.setTextViewText(R.id.notification_artifact, update.artifactLabel());
        views.setTextViewText(R.id.notification_workspace_name, update.activeWorkspace().name);
        views.setTextViewText(R.id.notification_workspace_index, update.workspacePositionText());
        bindPhases(views, update, 2);
        bindWorkspace(views, update);
        if (largeText) {
            views.setTextViewText(R.id.notification_metrics_summary,
                    "Tests " + update.testsMetricText() + " · " + update.warnings + " warnings · "
                            + update.errors + " errors · " + update.elapsedText());
        } else {
            views.setTextViewText(R.id.notification_metric_tests, update.testsMetricText());
            views.setTextViewText(R.id.notification_metric_warnings, Integer.toString(update.warnings));
            views.setTextViewText(R.id.notification_metric_errors, Integer.toString(update.errors));
            views.setTextViewText(R.id.notification_metric_elapsed, update.elapsedText());
        }
        bindActions(views, update);
    }

    private void bindPhases(RemoteViews views, HudUpdate update, int maxRows) {
        List<HudUpdate.Step> visible = visibleSteps(update, maxRows);
        for (int i = 0; i < PHASE_ROWS.length; i++) {
            if (i >= visible.size()) {
                views.setViewVisibility(PHASE_ROWS[i], View.GONE);
                continue;
            }
            HudUpdate.Step step = visible.get(i);
            views.setViewVisibility(PHASE_ROWS[i], View.VISIBLE);
            String[] label = splitStepLabel(step.label);
            views.setTextViewText(PHASE_NAMES[i], label[0]);
            views.setTextViewText(PHASE_DETAILS[i], phaseDetail(update, step, label[1]));
            int icon = step.succeeded() ? R.drawable.ic_phase_done
                    : step.failed() ? R.drawable.ic_phase_failed
                    : step.running() ? R.drawable.ic_phase_active
                    : R.drawable.ic_phase_pending;
            views.setImageViewResource(PHASE_MARKS[i], icon);
            int color = step.succeeded() ? context.getColor(R.color.notification_success)
                    : step.failed() ? context.getColor(R.color.notification_error)
                    : step.running() ? accentColor(update.notificationMode())
                    : context.getColor(R.color.notification_soft);
            int background = step.succeeded() ? R.drawable.notification_mark_done
                    : step.failed() ? R.drawable.notification_mark_failed
                    : step.running() ? R.drawable.notification_mark_active
                    : R.drawable.notification_mark_pending;
            views.setInt(PHASE_MARKS[i], "setColorFilter", color);
            views.setInt(PHASE_MARKS[i], "setBackgroundResource", background);
        }
    }

    private List<HudUpdate.Step> visibleSteps(HudUpdate update, int maxRows) {
        List<HudUpdate.Step> result = new ArrayList<>();
        if (update.steps.size() <= maxRows) {
            result.addAll(update.steps);
            return result;
        }
        int focus = -1;
        for (int i = 0; i < update.steps.size(); i++) {
            HudUpdate.Step step = update.steps.get(i);
            if (step.running() || step.failed()) {
                focus = i;
                break;
            }
        }
        if (focus < 0) focus = update.succeeded() ? update.steps.size() - 1 : 0;
        int start = Math.max(0, Math.min(update.steps.size() - maxRows, focus - 1));
        for (int i = start; i < Math.min(update.steps.size(), start + maxRows); i++) {
            result.add(update.steps.get(i));
        }
        return result;
    }

    private String phaseDetail(HudUpdate update, HudUpdate.Step step, String scope) {
        String prefix = scope.isEmpty() ? "" : scope + " · ";
        if (step.failed()) return prefix + "Failed";
        if (step.succeeded()) return prefix + "Complete";
        if (step.skipped()) return prefix + "Skipped";
        if (step.running()) {
            if (step == update.activeStep() && update.current >= 0 && update.total > 0) {
                return prefix + update.current + "/" + update.total + " " + update.unitLabel;
            }
            return prefix + (step.progress > 0 ? step.progress + "%" : "Running");
        }
        return prefix + "Waiting";
    }

    private void bindWorkspace(RemoteViews views, HudUpdate update) {
        HudUpdate.Workspace workspace = update.activeWorkspace();
        int accent = accentColor(update.notificationMode());
        int icon = workspace.done() ? R.drawable.ic_phase_done
                : workspace.failed() ? R.drawable.ic_phase_failed
                : workspace.active() ? R.drawable.ic_phase_active
                : R.drawable.ic_phase_pending;
        int color = workspace.done() ? context.getColor(R.color.notification_success)
                : workspace.failed() ? context.getColor(R.color.notification_error)
                : workspace.active() ? accent : context.getColor(R.color.notification_soft);
        int background = workspace.done() ? R.drawable.notification_mark_done
                : workspace.failed() ? R.drawable.notification_mark_failed
                : workspace.active() ? R.drawable.notification_mark_active
                : R.drawable.notification_mark_pending;
        views.setImageViewResource(R.id.notification_workspace_mark, icon);
        views.setInt(R.id.notification_workspace_mark, "setColorFilter", color);
        views.setInt(R.id.notification_workspace_mark, "setBackgroundResource", background);
        views.setTextViewText(R.id.notification_workspace_current, workspace.name);
        views.setTextViewText(R.id.notification_workspace_meta, workspace.meta);
        String workspaceProgress = update.current >= 0 && update.total > 0
                ? update.current + "/" + update.total
                : workspace.progress + "%";
        views.setTextViewText(R.id.notification_workspace_progress, workspaceProgress);
        String remaining = update.remainingWorkspacesText();
        views.setTextViewText(R.id.notification_workspace_remaining, remaining);
        views.setViewVisibility(R.id.notification_workspace_remaining, remaining.isEmpty() ? View.GONE : View.VISIBLE);
    }

    private void bindActions(RemoteViews views, HudUpdate update) {
        List<ActionSpec> actions = actionsFor(update);
        int accent = accentColor(update.notificationMode());
        for (int i = 0; i < ACTION_VIEWS.length; i++) {
            ActionSpec action = actions.get(i);
            views.setTextViewText(ACTION_VIEWS[i], action.label);
            views.setTextColor(ACTION_VIEWS[i], action.danger
                    ? context.getColor(R.color.notification_error) : accent);
            views.setOnClickPendingIntent(ACTION_VIEWS[i], action.intent);
            views.setContentDescription(ACTION_VIEWS[i], action.label + " for " + update.notificationTaskTitle());
        }
    }

    private List<ActionSpec> actionsFor(HudUpdate update) {
        List<ActionSpec> result = new ArrayList<>(3);
        String mode = update.notificationMode();
        if (mode.equals("running") || (mode.equals("warning") && !update.isTerminal())) {
            result.add(new ActionSpec("Open log", openLogIntent(update.runId), false));
            result.add(new ActionSpec("Copy summary", copySummaryIntent(update, "Summary copied"), false));
            result.add(new ActionSpec("Cancel", cancelRunIntent(update.runId), true));
        } else if (mode.equals("success") || mode.equals("warning")) {
            result.add(new ActionSpec("Open app", openRunIntent(update.runId), false));
            result.add(new ActionSpec("Copy summary", copySummaryIntent(update, "Summary copied"), false));
            result.add(new ActionSpec("Dismiss", dismissIntent(update.runId), false));
        } else if (mode.equals("failed") && !update.rollbackState()) {
            result.add(new ActionSpec("Open log", openLogIntent(update.runId), false));
            result.add(new ActionSpec("Copy error", copySummaryIntent(update, "Error copied"), false));
            result.add(new ActionSpec("Retry", retryIntent(update.runId), false));
        } else if (mode.equals("rollback")) {
            result.add(new ActionSpec("Open log", openLogIntent(update.runId), false));
            result.add(new ActionSpec("Copy status", copySummaryIntent(update, "Rollback status copied"), false));
            result.add(new ActionSpec("Keep visible", keepVisibleIntent(update.runId), false));
        } else if (mode.equals("recovered")) {
            result.add(new ActionSpec("Open log", openLogIntent(update.runId), false));
            result.add(new ActionSpec("Copy summary", copySummaryIntent(update, "Recovery summary copied"), false));
            result.add(new ActionSpec("Dismiss", dismissIntent(update.runId), false));
        } else if (mode.equals("failed")) {
            result.add(new ActionSpec("Open log", openLogIntent(update.runId), false));
            result.add(new ActionSpec("Copy recovery", copySummaryIntent(update, "Recovery status copied"), false));
            result.add(new ActionSpec("Keep visible", keepVisibleIntent(update.runId), false));
        } else {
            result.add(new ActionSpec("Open log", openLogIntent(update.runId), false));
            result.add(new ActionSpec("Copy summary", copySummaryIntent(update, "Summary copied"), false));
            result.add(new ActionSpec("Dismiss", dismissIntent(update.runId), false));
        }
        return result;
    }

    private void refreshGroupSummary() {
        List<RunRecord> active = RunStore.activeAll(context);
        if (active.size() < 2) {
            manager.cancel(SUMMARY_NOTIFICATION_ID);
            return;
        }

        Notification.InboxStyle style = new Notification.InboxStyle()
                .setBigContentTitle(active.size() + " active Devtool runs")
                .setSummaryText("Tap to open run history");
        int errors = 0;
        int warnings = 0;
        for (int i = 0; i < Math.min(active.size(), 6); i++) {
            RunRecord record = active.get(i);
            String scope = record.target == null || record.target.trim().isEmpty() ? record.repo : record.target;
            style.addLine(record.progress + "% · " + scope + " · " + record.phase);
            errors += record.errors;
            warnings += record.warnings;
        }

        String health = errors > 0 ? errors + " errors" : warnings > 0 ? warnings + " warnings" : "No reported errors";
        Notification summary = new Notification.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_devtool_notification)
                .setContentTitle(active.size() + " active Devtool runs")
                .setContentText(health)
                .setCategory(Notification.CATEGORY_PROGRESS)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setShowWhen(false)
                .setGroup(GROUP_KEY)
                .setGroupSummary(true)
                .setStyle(style)
                .setContentIntent(openHistoryIntent())
                .build();
        manager.notify(SUMMARY_NOTIFICATION_ID, summary);
    }

    static int notificationId(String runId) {
        return RunIdentity.notificationId(runId);
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

    private PendingIntent openHistoryIntent() {
        Intent intent = new Intent(context, MainActivity.class)
                .putExtra("open_history", true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(context, SUMMARY_NOTIFICATION_ID ^ 0x2a91, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private PendingIntent openRunIntent(String runId) {
        Intent intent = new Intent(context, LogViewerActivity.class)
                .putExtra(LogViewerActivity.EXTRA_RUN_ID, runId)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(context, runId.hashCode() ^ 0x0c41, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private PendingIntent openLogIntent(String runId) {
        Intent intent = new Intent(context, LogViewerActivity.class)
                .putExtra(LogViewerActivity.EXTRA_RUN_ID, runId)
                .setData(android.net.Uri.parse("devtool://logs/run/" + android.net.Uri.encode(runId)))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(context, runId.hashCode() ^ 0x1f31, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private PendingIntent copySummaryIntent(HudUpdate update, String toast) {
        Intent intent = new Intent(context, ActionReceiver.class)
                .setAction(ActionReceiver.ACTION_COPY_SUMMARY)
                .putExtra("run_id", update.runId)
                .putExtra("summary", update.copySummaryText())
                .putExtra("toast", toast);
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

    private PendingIntent cancelRunIntent(String runId) {
        Intent intent = new Intent(context, ActionReceiver.class)
                .setAction(ActionReceiver.ACTION_CANCEL_RUN)
                .putExtra("run_id", runId);
        return PendingIntent.getBroadcast(context, runId.hashCode() ^ 0x6b27, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private PendingIntent retryIntent(String runId) {
        Intent intent = new Intent(context, ActionReceiver.class)
                .setAction(ActionReceiver.ACTION_RETRY)
                .putExtra("run_id", runId);
        return PendingIntent.getBroadcast(context, runId.hashCode() ^ 0x7c39, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private PendingIntent keepVisibleIntent(String runId) {
        Intent intent = new Intent(context, ActionReceiver.class)
                .setAction(ActionReceiver.ACTION_KEEP_VISIBLE)
                .putExtra("run_id", runId);
        return PendingIntent.getBroadcast(context, runId.hashCode() ^ 0x8d4b, intent,
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
        return accentColor(update.notificationMode());
    }

    private int accentColor(String mode) {
        if (mode.equals("success")) return context.getColor(R.color.notification_success);
        if (mode.equals("warning")) return context.getColor(R.color.notification_warning);
        if (mode.equals("failed")) return context.getColor(R.color.notification_error);
        if (mode.equals("rollback")) return context.getColor(R.color.notification_rollback);
        if (mode.equals("recovered")) return context.getColor(R.color.notification_recovered);
        if (mode.equals("stopped")) return context.getColor(R.color.notification_soft);
        return context.getColor(R.color.notification_running);
    }

    private int cardDrawable(String mode) {
        if (mode.equals("success")) return R.drawable.notification_card_success;
        if (mode.equals("warning")) return R.drawable.notification_card_warning;
        if (mode.equals("failed")) return R.drawable.notification_card_failed;
        if (mode.equals("rollback")) return R.drawable.notification_card_rollback;
        if (mode.equals("recovered")) return R.drawable.notification_card_recovered;
        if (mode.equals("stopped")) return R.drawable.notification_card_stopped;
        return R.drawable.notification_card_running;
    }

    private int badgeDrawable(String mode) {
        if (mode.equals("success")) return R.drawable.notification_badge_success;
        if (mode.equals("warning")) return R.drawable.notification_badge_warning;
        if (mode.equals("failed")) return R.drawable.notification_badge_failed;
        if (mode.equals("rollback")) return R.drawable.notification_badge_rollback;
        if (mode.equals("recovered")) return R.drawable.notification_badge_recovered;
        if (mode.equals("stopped")) return R.drawable.notification_badge_stopped;
        return R.drawable.notification_badge_running;
    }

    private static String[] splitStepLabel(String value) {
        String clean = value == null ? "Step" : value.trim();
        int divider = clean.indexOf(" · ");
        if (divider < 0) return new String[]{clean, ""};
        return new String[]{clean.substring(0, divider), clean.substring(divider + 3)};
    }

    private static final class ActionSpec {
        final String label;
        final PendingIntent intent;
        final boolean danger;

        ActionSpec(String label, PendingIntent intent, boolean danger) {
            this.label = label;
            this.intent = intent;
            this.danger = danger;
        }
    }
}
