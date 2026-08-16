package io.github.mikeceead.devtoolhud;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.text.DateFormat;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/** Central cockpit for Phase 34-44 debug evidence and guided fixes. */
public final class DebugWorkbenchActivity extends Activity {
    private static final String STATE_SCROLL_Y = "debug_workbench_scroll_y";
    private UiKit ui;
    private boolean receiverRegistered;
    private ScrollView activeScroll;
    private int pendingScrollY;

    private final BroadcastReceiver updates = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) { rebuildUi(); }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        if (state != null) pendingScrollY = Math.max(0, state.getInt(STATE_SCROLL_Y, 0));
        rebuildUi();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        outState.putInt(STATE_SCROLL_Y, activeScroll == null ? pendingScrollY : activeScroll.getScrollY());
    }

    @Override
    @SuppressLint("UnspecifiedRegisterReceiverFlag")
    protected void onStart() {
        super.onStart();
        if (!receiverRegistered) {
            IntentFilter filter = new IntentFilter(DebugRecorder.ACTION_UPDATED);
            filter.addAction(RunStore.ACTION_UPDATED);
            filter.addAction(BridgeTokenManager.ACTION_UPDATED);
            filter.addAction(BridgeRoundtripProbe.ACTION_UPDATED);
            filter.addAction(RootBridgeManager.ACTION_UPDATED);
            filter.addAction(RepositoryStore.ACTION_UPDATED);
            filter.addAction(RepositoryWorkspaceStore.ACTION_UPDATED);
            filter.addAction(RepositoryDoctor.ACTION_UPDATED);
            filter.addAction(RootLogIndexer.ACTION_UPDATED);
            filter.addAction(ValidationStore.ACTION_UPDATED);
            if (Build.VERSION.SDK_INT >= 33) registerReceiver(updates, filter, Context.RECEIVER_NOT_EXPORTED);
            else registerReceiver(updates, filter);
            receiverRegistered = true;
        }
    }

    @Override
    protected void onStop() {
        if (receiverRegistered) {
            unregisterReceiver(updates);
            receiverRegistered = false;
        }
        super.onStop();
    }

    private void rebuildUi() {
        if (activeScroll != null) pendingScrollY = activeScroll.getScrollY();
        ui = new UiKit(this);
        getWindow().setStatusBarColor(ui.palette.background);
        getWindow().setNavigationBarColor(ui.palette.background);
        activeScroll = (ScrollView) buildRoot();
        setContentView(activeScroll);
        restoreScrollPosition();
    }

    private void restoreScrollPosition() {
        if (activeScroll == null || pendingScrollY <= 0) return;
        final int scrollY = pendingScrollY;
        activeScroll.post(() -> activeScroll.scrollTo(0, scrollY));
    }

    private View buildRoot() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(ui.palette.background);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(ui.contentHorizontalPadding(), ui.dp(10), ui.contentHorizontalPadding(), ui.dp(28));
        content.setOnApplyWindowInsetsListener((view, insets) -> {
            int top = 0;
            int bottom = 0;
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                top = bars.top;
                bottom = bars.bottom;
            }
            view.setPadding(ui.contentHorizontalPadding(), ui.dp(10) + top,
                    ui.contentHorizontalPadding(), ui.dp(28) + bottom);
            return insets;
        });
        content.addView(header());
        DebugWorkbenchHealth.Snapshot snapshot = DebugWorkbenchHealth.assess(this);
        content.addView(healthCard(snapshot));
        content.addView(guidedFixesCard(snapshot));
        content.addView(launcherCard());
        content.addView(runTraceCard(snapshot));
        content.addView(repoDiagnosticsCard(snapshot));
        content.addView(validationHealthCard(snapshot));
        content.addView(recentEventsCard(snapshot));
        content.addView(bundleCard());
        scroll.addView(content);
        return scroll;
    }

    private View header() {
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.addView(ui.text("DEVTOOL", UiTypography.Role.LABEL, ui.palette.cyan));
        TextView title = ui.text("Debug Workbench", UiTypography.Role.PAGE_TITLE, ui.palette.text);
        if (Build.VERSION.SDK_INT >= 28) title.setAccessibilityHeading(true);
        header.addView(title);
        header.addView(ui.text("Run trace, bridge probe, repository doctor, queue recovery and recent debug events in one cockpit.",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        header.addView(ui.secondaryButton("Back to app", v -> finish()));
        return header;
    }

    private View healthCard(DebugWorkbenchHealth.Snapshot snapshot) {
        LinearLayout card = ui.surface();
        card.addView(ui.text("Current health", UiTypography.Role.CARD_TITLE, ui.palette.text));
        card.addView(ui.text(snapshot.summary(), UiTypography.Role.LARGE_VALUE, color(snapshot.status())));
        card.addView(ui.text("Schema " + snapshot.schemaVersion + " · "
                        + snapshot.problemDebugEvents + " recent problem events · "
                        + snapshot.repoFailures + " repo failures · " + snapshot.repoWarnings + " repo warnings",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        for (DebugWorkbenchHealth.Component component : snapshot.components) {
            TextView line = ui.text(component.name + " · " + component.label + " · " + component.detail,
                    UiTypography.Role.BODY_SMALL, color(component.status));
            line.setSingleLine(false);
            card.addView(line);
        }
        card.addView(ui.secondaryButton("Copy health report", v -> copyText("Debug Workbench health", DebugWorkbenchHealth.plainText(this))));
        return card;
    }


    private View guidedFixesCard(DebugWorkbenchHealth.Snapshot snapshot) {
        LinearLayout card = ui.surface();
        card.addView(ui.text("Guided Fixes", UiTypography.Role.CARD_TITLE, ui.palette.text));
        List<DebugGuidedFix> fixes = DebugGuidedFix.from(this, snapshot);
        if (fixes.isEmpty()) {
            card.addView(ui.text("No guided fixes are active. The workbench still keeps health checks, run trace and recent events ready.",
                    UiTypography.Role.BODY_SMALL, ui.palette.muted));
            return card;
        }
        card.addView(ui.text("Each fix explains what happened, what it means, the evidence and one safe next action.",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        int shown = 0;
        for (DebugGuidedFix fix : fixes) {
            LinearLayout item = miniCard();
            item.addView(ui.text(fix.title, UiTypography.Role.LABEL, color(fix.severity)));
            item.addView(ui.text("Issue: " + fix.issueKey, UiTypography.Role.CAPTION, ui.palette.muted));
            item.addView(ui.text("What happened: " + fix.whatHappened, UiTypography.Role.BODY_SMALL, ui.palette.text));
            item.addView(ui.text("What it means: " + fix.whatItMeans, UiTypography.Role.BODY_SMALL, ui.palette.muted));
            item.addView(ui.text("Evidence: " + limit(fix.evidence, 420), UiTypography.Role.CAPTION, ui.palette.muted));
            item.addView(ui.text("Safe next action: " + fix.nextAction, UiTypography.Role.BODY_SMALL, color(fix.severity)));
            item.addView(ui.secondaryButton(fix.actionLabel, v -> performGuidedFixAction(fix)));
            item.addView(ui.secondaryButton("Copy diagnosis", v -> copyText("Guided fix", fix.plainText())));
            card.addView(item);
            shown++;
            if (shown >= 8) break;
        }
        card.addView(ui.secondaryButton("Copy guided fixes", v -> copyText("Guided fixes", guidedFixesText(fixes))));
        return card;
    }

    private View launcherCard() {
        LinearLayout card = ui.surface();
        card.addView(ui.text("Probes and entry points", UiTypography.Role.CARD_TITLE, ui.palette.text));
        card.addView(ui.text("Safe shortcuts into the diagnostic machinery. The bridge probe is the only command this screen can launch.",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        card.addView(ui.button("Run bridge roundtrip probe", bridgeColor(), v -> runBridgeProbe()));
        card.addView(ui.secondaryButton("Open Termux", v -> {
            if (!TermuxCommandRunner.openTermux(this)) Toast.makeText(this, "Termux could not be opened", Toast.LENGTH_LONG).show();
        }));
        card.addView(ui.secondaryButton("Open Settings diagnostics", v -> openMainPage(4)));
        card.addView(ui.secondaryButton("Open Validation Workbench", v -> openValidationWorkbench()));
        card.addView(ui.secondaryButton("Open Repositories", v -> openMainPage(3)));
        card.addView(ui.secondaryButton("Open Logs", v -> openMainPage(2)));
        return card;
    }

    private View runTraceCard(DebugWorkbenchHealth.Snapshot snapshot) {
        LinearLayout card = ui.surface();
        card.addView(ui.text("Run Command Trace", UiTypography.Role.CARD_TITLE, ui.palette.text));
        RunRecord run = snapshot.activeRun != null ? snapshot.activeRun : snapshot.latestRun;
        if (run == null) {
            card.addView(ui.text("No run trace yet. Start a devtool run to record launch checkpoints.", UiTypography.Role.BODY_SMALL, ui.palette.muted));
            card.addView(ui.secondaryButton("Create a run", v -> openMainPage(1)));
            return card;
        }
        String activePrefix = snapshot.activeRun != null ? "Active" : "Latest";
        card.addView(ui.text(activePrefix + " run · " + run.runId, UiTypography.Role.LABEL, color(run.status)));
        card.addView(ui.text(run.status + " · " + run.phase + " · " + run.progress + "% · " + run.detail,
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        if (!run.launchLog.trim().isEmpty()) {
            TextView log = ui.text(limit(run.launchLog, 1600), UiTypography.Role.CAPTION, ui.palette.text);
            log.setTypeface(android.graphics.Typeface.MONOSPACE);
            log.setTextIsSelectable(true);
            log.setBackground(ui.flat(ui.palette.surfaceRaised, 8));
            log.setPadding(ui.dp(10), ui.dp(8), ui.dp(10), ui.dp(8));
            card.addView(log);
            card.addView(ui.secondaryButton("Copy run trace", v -> copyText("Run command trace", run.launchLog)));
        } else {
            card.addView(ui.text("No launch-log checkpoints were retained for this run.", UiTypography.Role.BODY_SMALL, ui.palette.muted));
        }
        card.addView(ui.secondaryButton("Open run log", v -> openRun(run.runId)));
        return card;
    }

    private View repoDiagnosticsCard(DebugWorkbenchHealth.Snapshot snapshot) {
        LinearLayout card = ui.surface();
        card.addView(ui.text("Repo Manager Doctor", UiTypography.Role.CARD_TITLE, ui.palette.text));
        if (snapshot.repositories.isEmpty()) {
            card.addView(ui.text("No repositories are indexed yet.", UiTypography.Role.BODY_SMALL, ui.palette.muted));
            card.addView(ui.secondaryButton("Discover repositories", v -> openMainPage(3)));
            return card;
        }
        int shown = 0;
        for (RepositoryRecord repository : snapshot.repositories) {
            RepositoryPathResolution path = RepositoryPathResolver.resolve(repository);
            RepoQueueRecovery.QueueHealth queue = RepoQueueRecovery.assess(this, repository);
            boolean interesting = !path.status.equals(RepositoryPathResolution.STATUS_PASS) || !queue.status.equals("pass")
                    || !repository.lastError.trim().isEmpty();
            if (!interesting && shown >= 3) continue;
            LinearLayout item = miniCard();
            item.addView(ui.text(repository.name() + " · " + repository.location().displayEnvironment(),
                    UiTypography.Role.LABEL, interesting ? color(path.status.equals("fail") || queue.status.equals("fail") ? "fail" : "warn") : ui.palette.text));
            item.addView(ui.text("Path: " + path.status + " · " + path.summary, UiTypography.Role.BODY_SMALL, ui.palette.muted));
            item.addView(ui.text("Queue: " + queue.status + " · " + queue.summary, UiTypography.Role.BODY_SMALL, ui.palette.muted));
            if (!repository.lastError.trim().isEmpty()) item.addView(ui.text("Last error: " + repository.lastError,
                    UiTypography.Role.BODY_SMALL, ui.palette.red));
            item.addView(ui.secondaryButton("Open repository doctor", v -> openRepository(repository.repositoryId)));
            card.addView(item);
            shown++;
            if (shown >= 6) break;
        }
        card.addView(ui.secondaryButton("Open repositories", v -> openMainPage(3)));
        return card;
    }

    private View validationHealthCard(DebugWorkbenchHealth.Snapshot snapshot) {
        LinearLayout card = ui.surface();
        card.addView(ui.text("Validation Workbench health", UiTypography.Role.CARD_TITLE, ui.palette.text));
        ValidationSessionRecord session = snapshot.activeValidation != null ? snapshot.activeValidation : snapshot.latestValidation;
        if (session == null) {
            card.addView(ui.text("No validation session has been indexed yet.", UiTypography.Role.BODY_SMALL, ui.palette.muted));
            card.addView(ui.secondaryButton("Open Validation Workbench", v -> openValidationWorkbench()));
            return card;
        }
        card.addView(ui.text(session.title(), UiTypography.Role.LABEL, color(session.effectiveState)));
        card.addView(ui.text(session.effectiveState + " · revision " + session.revision + " · "
                        + session.completedPhases + "/" + session.phaseTotal + " phases",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        if (!session.lastError.trim().isEmpty()) card.addView(ui.text(session.lastError, UiTypography.Role.BODY_SMALL, ui.palette.red));
        if (!session.lastReason.trim().isEmpty()) card.addView(ui.text(session.lastReason, UiTypography.Role.BODY_SMALL, ui.palette.yellow));
        card.addView(ui.secondaryButton("Open Validation Workbench", v -> openValidationWorkbench()));
        return card;
    }

    private View recentEventsCard(DebugWorkbenchHealth.Snapshot snapshot) {
        LinearLayout card = ui.surface();
        card.addView(ui.text("Recent debug events", UiTypography.Role.CARD_TITLE, ui.palette.text));
        if (snapshot.recentEvents.isEmpty()) {
            card.addView(ui.text("No debug recorder events yet.", UiTypography.Role.BODY_SMALL, ui.palette.muted));
            return card;
        }
        int shown = 0;
        for (DebugEventRecord event : snapshot.recentEvents) {
            if (!event.problem() && shown >= 8) continue;
            TextView line = ui.text(time(event.timestampMs) + " · " + event.severity + "/" + event.category + " · "
                            + event.eventType + " · " + event.message,
                    UiTypography.Role.CAPTION, event.problem() ? color(event.severity) : ui.palette.muted);
            line.setSingleLine(false);
            line.setEllipsize(TextUtils.TruncateAt.END);
            card.addView(line);
            shown++;
            if (shown >= 18) break;
        }
        card.addView(ui.secondaryButton("Copy recent debug events", v -> copyText("Recent debug events", debugEventsText(snapshot.recentEvents))));
        return card;
    }

    private View bundleCard() {
        LinearLayout card = ui.surface();
        card.addView(ui.text("Export Debug Bundle", UiTypography.Role.CARD_TITLE, ui.palette.text));
        card.addView(ui.text("Copies or shares the redacted support bundle with debug_workbench, run trace, repository doctor, path resolver, queue health and recent debug events.",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        card.addView(ui.button("Copy support bundle", ui.palette.green, v -> copySupportBundle()));
        card.addView(ui.secondaryButton("Share support bundle", v -> shareSupportBundle()));
        return card;
    }

    private LinearLayout miniCard() {
        LinearLayout item = new LinearLayout(this);
        item.setOrientation(LinearLayout.VERTICAL);
        item.setPadding(ui.dp(12), ui.dp(10), ui.dp(12), ui.dp(10));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, ui.dp(6), 0, ui.dp(6));
        item.setLayoutParams(params);
        item.setBackground(ui.flat(ui.palette.surfaceRaised, 10));
        return item;
    }


    private void performGuidedFixAction(DebugGuidedFix fix) {
        String kind = fix == null ? "" : fix.actionKind;
        if (kind.equals("bridge_probe")) {
            runBridgeProbe();
        } else if (kind.equals("settings")) {
            openMainPage(4);
        } else if (kind.equals("notification_settings")) {
            openOwnAppDetails();
        } else if (kind.equals("termux")) {
            if (!TermuxCommandRunner.openTermux(this)) Toast.makeText(this, "Termux could not be opened", Toast.LENGTH_LONG).show();
        } else if ((kind.equals("repository") || kind.equals("repositories")) && fix != null && !fix.repositoryId.isEmpty()) {
            openRepository(fix.repositoryId);
        } else if (kind.equals("repository") || kind.equals("repositories")) {
            openMainPage(3);
        } else if (kind.equals("run_log") && fix != null && !fix.runId.isEmpty()) {
            openRun(fix.runId);
        } else if (kind.equals("run_log") || kind.equals("logs")) {
            openMainPage(2);
        } else if (kind.equals("validation_workbench")) {
            openValidationWorkbench();
        } else if (kind.equals("copy") && fix != null) {
            copyText("Guided fix", fix.plainText());
        } else {
            rebuildUi();
        }
    }

    private String guidedFixesText(List<DebugGuidedFix> fixes) {
        StringBuilder text = new StringBuilder("Guided fixes\n");
        int count = 0;
        for (DebugGuidedFix fix : fixes) {
            text.append("\n").append(fix.plainText());
            if (++count >= 12) break;
        }
        return text.toString();
    }

    private void runBridgeProbe() {
        String state = BridgeRoundtripProbe.state(this);
        if (state.equals("permission-needed")) {
            openOwnAppDetails();
            return;
        }
        TermuxCommandRunner.LaunchResult result = BridgeRoundtripProbe.start(this);
        Toast.makeText(this, result.started ? "Bridge roundtrip probe started" : result.error,
                result.started ? Toast.LENGTH_SHORT : Toast.LENGTH_LONG).show();
        rebuildUi();
    }

    private int bridgeColor() {
        String state = BridgeRoundtripProbe.state(this);
        if (state.equals("passed")) return ui.palette.green;
        if (state.equals("failed") || state.equals("permission-needed") || state.equals("termux-missing")) return ui.palette.red;
        return ui.palette.cyan;
    }

    private void openMainPage(int page) {
        startActivity(new Intent(this, MainActivity.class).putExtra("open_page", page));
    }

    private void openValidationWorkbench() {
        Intent intent = new Intent(this, MainActivity.class).putExtra("open_page", 1).putExtra("run_section", "validation");
        startActivity(intent);
    }

    private void openRun(String runId) {
        startActivity(new Intent(this, LogViewerActivity.class).putExtra(LogViewerActivity.EXTRA_RUN_ID, runId));
    }

    private void openRepository(String repositoryId) {
        startActivity(new Intent(this, RepositoryDetailActivity.class).putExtra("repository_id", repositoryId));
    }

    private void openOwnAppDetails() {
        Intent intent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                .setData(Uri.parse("package:" + getPackageName()));
        startActivity(intent);
    }

    private void copySupportBundle() {
        String bundle = SupportBundle.build(this);
        copyText("Devtool support bundle", bundle);
        DebugNotificationController.postSupportBundleReady(this, "copied", bundle);
    }

    private void shareSupportBundle() {
        String bundle = SupportBundle.build(this);
        shareText("Devtool support bundle", bundle);
        DebugNotificationController.postSupportBundleReady(this, "shared", bundle);
    }

    private void copyText(String label, String value) {
        ClipboardManager clipboard = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        if (clipboard != null) clipboard.setPrimaryClip(ClipData.newPlainText(label, value == null ? "" : value));
        Toast.makeText(this, label + " copied", Toast.LENGTH_SHORT).show();
    }

    private void shareText(String title, String value) {
        Intent intent = new Intent(Intent.ACTION_SEND)
                .setType("text/plain")
                .putExtra(Intent.EXTRA_SUBJECT, title)
                .putExtra(Intent.EXTRA_TEXT, value == null ? "" : value);
        startActivity(Intent.createChooser(intent, title));
    }

    private String debugEventsText(List<DebugEventRecord> events) {
        StringBuilder text = new StringBuilder("Recent debug events\n");
        int count = 0;
        for (DebugEventRecord event : events) {
            text.append(time(event.timestampMs)).append(' ')
                    .append(event.severity).append('/').append(event.category).append(' ')
                    .append(event.eventType).append(" run=").append(event.runId)
                    .append(" repo=").append(event.repositoryId)
                    .append(" op=").append(event.operationId)
                    .append(" msg=").append(event.message).append('\n');
            if (++count >= 80) break;
        }
        return text.toString();
    }

    private int color(String status) {
        String clean = status == null ? "" : status.toLowerCase(Locale.ROOT);
        if (clean.equals("fail") || clean.equals("failed") || clean.equals("error") || clean.equals("fatal")) return ui.palette.red;
        if (clean.equals("warn") || clean.equals("warning") || clean.equals("running") || clean.equals("waiting")) return ui.palette.yellow;
        if (clean.equals("pass") || clean.equals("success") || clean.equals("ok") || clean.equals("completed")) return ui.palette.green;
        return ui.palette.text;
    }

    private String time(long timestampMs) {
        if (timestampMs <= 0L) return "unknown";
        return DateFormat.getTimeInstance(DateFormat.SHORT).format(new Date(timestampMs));
    }

    private static String limit(String value, int max) {
        String text = value == null ? "" : value.trim();
        return text.length() <= max ? text : text.substring(0, max) + "\n…";
    }

    @Override
    public boolean onKeyUp(int keyCode, KeyEvent event) {
        if (event.isCtrlPressed() && keyCode == KeyEvent.KEYCODE_L) {
            openMainPage(2);
            return true;
        }
        if (event.isCtrlPressed() && keyCode == KeyEvent.KEYCODE_R) {
            openMainPage(3);
            return true;
        }
        if (keyCode == KeyEvent.KEYCODE_ESCAPE) {
            finish();
            return true;
        }
        return super.onKeyUp(keyCode, event);
    }
}
