package io.github.mikeceead.devtoolhud;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.text.DateFormat;
import java.util.Collections;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class LogViewerActivity extends Activity {
    static final String EXTRA_RUN_ID = "run_id";
    private static final int LOG_PAGE_SIZE = 100;
    private static final int RAW_LINE_LIMIT = 600;
    private static final int RAW_TEXT_LIMIT = 1_500_000;
    private static final long UPDATE_DEBOUNCE_MS = 250L;
    private UiKit ui;
    private String runId = "";
    private int tab;
    private boolean registered;
    private int timelineLimit = LOG_PAGE_SIZE;
    private int problemsLimit = LOG_PAGE_SIZE;
    private int loadGeneration;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService logExecutor = Executors.newSingleThreadExecutor();
    private final Runnable debouncedRebuild = this::rebuild;

    private final BroadcastReceiver updates = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            String changed = intent.getStringExtra("run_id");
            if (changed == null || changed.isEmpty() || changed.equals(runId)) scheduleRebuild();
        }
    };

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        runId = resolveRunId(getIntent());
        rebuild();
    }

    @Override @SuppressLint("UnspecifiedRegisterReceiverFlag") protected void onStart() {
        super.onStart();
        if (!registered) {
            IntentFilter filter = new IntentFilter(RunStore.ACTION_UPDATED);
            if (Build.VERSION.SDK_INT >= 33) registerReceiver(updates, filter, Context.RECEIVER_NOT_EXPORTED);
            else registerReceiver(updates, filter);
            registered = true;
        }
    }

    @Override protected void onStop() {
        if (registered) {
            unregisterReceiver(updates);
            registered = false;
        }
        mainHandler.removeCallbacks(debouncedRebuild);
        super.onStop();
    }

    @Override protected void onDestroy() {
        loadGeneration++;
        logExecutor.shutdownNow();
        super.onDestroy();
    }

    private void scheduleRebuild() {
        mainHandler.removeCallbacks(debouncedRebuild);
        mainHandler.postDelayed(debouncedRebuild, UPDATE_DEBOUNCE_MS);
    }

    private void rebuild() {
        ui = new UiKit(this);
        getWindow().setStatusBarColor(ui.palette.background);
        getWindow().setNavigationBarColor(ui.palette.background);
        RunRecord run = RunStore.find(this, runId);
        setContentView(build(run));
    }

    private View build(RunRecord run) {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(ui.palette.background);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                view.setPadding(0, bars.top, 0, bars.bottom);
            }
            return insets;
        });

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(ui.dp(18), ui.dp(16), ui.dp(18), ui.dp(10));
        if (run == null) {
            header.addView(ui.text("Log unavailable", UiTypography.Role.PAGE_TITLE, ui.palette.text));
            header.addView(ui.text("The requested run is no longer stored.", UiTypography.Role.BODY, ui.palette.muted));
            root.addView(header);
            return root;
        }
        LinearLayout titleRow = new LinearLayout(this);
        titleRow.setOrientation(ui.verticalActions() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        TextView title = ui.text(run.title, UiTypography.Role.PAGE_TITLE, ui.palette.text);
        if (ui.verticalActions()) titleRow.addView(title);
        else titleRow.addView(title, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        titleRow.addView(ui.text((run.pinned ? "PINNED · " : "") + run.status.toUpperCase(Locale.ROOT),
                UiTypography.Role.CAPTION, run.pinned ? ui.palette.yellow : statusColor(run.status)));
        header.addView(titleRow);
        header.addView(ui.text(join(" · ", run.repo, run.target, run.projectType, run.executionEnvironment),
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        header.addView(ui.text(run.phase + " · " + duration(run.durationMs) + " · "
                        + run.errors + " errors · " + run.warnings + " warnings",
                UiTypography.Role.LABEL, ui.palette.muted));
        header.addView(ui.secondaryButton(run.pinned ? "Unpin indexed run" : "Pin indexed run", v -> {
            RunStore.setPinned(this, run.runId, !run.pinned);
            rebuild();
        }));
        root.addView(header);

        LinearLayout tabs = new LinearLayout(this);
        tabs.setOrientation(ui.twoPaneLayout() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        String[] labels = {"Timeline", "Problems", "Raw", "Metadata"};
        for (int i = 0; i < labels.length; i++) {
            final int selected = i;
            Button button = ui.navButton(R.drawable.ic_nav_history, labels[i], tab == i, v -> {
                tab = selected;
                if (tab == 0) timelineLimit = LOG_PAGE_SIZE;
                if (tab == 1) problemsLimit = LOG_PAGE_SIZE;
                rebuild();
            });
            if (ui.twoPaneLayout()) {
                button.setGravity(android.view.Gravity.START | android.view.Gravity.CENTER_VERTICAL);
                button.setCompoundDrawablesWithIntrinsicBounds(R.drawable.ic_nav_history, 0, 0, 0);
                button.setCompoundDrawablePadding(ui.dp(10));
                tabs.addView(button, new LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
            } else {
                tabs.addView(button, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            }
        }

        ScrollView scroll = new ScrollView(this);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(ui.contentHorizontalPadding(), ui.dp(4), ui.contentHorizontalPadding(), ui.dp(24));
        if (tab == 0) timeline(content, run, false);
        else if (tab == 1) timeline(content, run, true);
        else if (tab == 2) raw(content, run);
        else metadata(content, run);
        scroll.addView(content);

        if (ui.twoPaneLayout()) {
            LinearLayout body = new LinearLayout(this);
            body.setOrientation(LinearLayout.HORIZONTAL);
            tabs.setPadding(ui.dp(8), ui.dp(8), ui.dp(8), ui.dp(8));
            tabs.setBackgroundColor(ui.palette.surface);
            body.addView(tabs, new LinearLayout.LayoutParams(ui.dp(176),
                    LinearLayout.LayoutParams.MATCH_PARENT));
            body.addView(scroll, new LinearLayout.LayoutParams(0,
                    LinearLayout.LayoutParams.MATCH_PARENT, 1f));
            root.addView(body, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        } else {
            root.addView(tabs);
            root.addView(scroll, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        }
        return root;
    }

    private void timeline(LinearLayout content, RunRecord run, boolean problems) {
        int visibleLimit = problems ? problemsLimit : timelineLimit;
        LinearLayout loading = ui.surface();
        loading.addView(ui.text("Loading log entries...", UiTypography.Role.CARD_TITLE, ui.palette.text));
        loading.addView(ui.text("The app stays responsive while indexed log rows are read.",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        content.addView(loading);
        int generation = ++loadGeneration;
        logExecutor.execute(() -> {
            List<DevtoolDatabase.LogLine> lines = DevtoolDatabase.get(this)
                    .logLines(run.runId, problems, visibleLimit + 1, 0);
            mainHandler.post(() -> {
                if (generation != loadGeneration || isFinishing()) return;
                content.removeAllViews();
                renderTimeline(content, run, problems, lines, visibleLimit);
            });
        });
    }

    private void renderTimeline(LinearLayout content, RunRecord run, boolean problems,
                                List<DevtoolDatabase.LogLine> loaded, int visibleLimit) {
        boolean hasMore = loaded.size() > visibleLimit;
        List<DevtoolDatabase.LogLine> lines = hasMore ? loaded.subList(0, visibleLimit) : loaded;
        if (lines.isEmpty()) {
            LinearLayout empty = ui.surface();
            empty.addView(ui.text(problems ? "No problems recorded" : "No structured events recorded",
                    UiTypography.Role.CARD_TITLE, problems ? ui.palette.green : ui.palette.text));
            empty.addView(ui.text(problems ? "Warnings and errors from this run will appear here."
                            : "Raw output is still available in the Raw tab.",
                    UiTypography.Role.BODY_SMALL, ui.palette.muted));
            content.addView(empty);
            return;
        }
        for (DevtoolDatabase.LogLine line : lines) {
            LinearLayout card = ui.surface();
            LinearLayout heading = new LinearLayout(this);
            heading.setOrientation(LinearLayout.HORIZONTAL);
            heading.addView(ui.text(time(line.timestampMs), UiTypography.Role.CAPTION, ui.palette.muted));
            TextView severity = ui.text(line.severity.toUpperCase(Locale.ROOT), UiTypography.Role.CAPTION,
                    severityColor(line.severity));
            severity.setPadding(ui.dp(10), 0, 0, 0);
            heading.addView(severity);
            TextView category = ui.text(join(" · ", line.category, line.phase, line.source),
                    UiTypography.Role.CAPTION, ui.palette.muted);
            category.setGravity(android.view.Gravity.END);
            heading.addView(category, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            card.addView(heading);
            card.addView(ui.text(line.message, UiTypography.Role.BODY, ui.palette.text));
            String location = line.filePath.isEmpty() ? "" : line.filePath
                    + (line.lineNumber > 0 ? ":" + line.lineNumber : "")
                    + (line.diagnosticCode.isEmpty() ? "" : " · " + line.diagnosticCode);
            if (!location.isEmpty()) card.addView(ui.text(location, UiTypography.Role.CAPTION, ui.palette.muted));
            if (!line.raw.isEmpty() && !line.raw.equals(line.message)) {
                TextView raw = ui.text(line.raw, UiTypography.Role.BODY_SMALL, ui.palette.muted);
                raw.setTypeface(Typeface.MONOSPACE);
                raw.setTextIsSelectable(true);
                card.addView(raw);
            }
            content.addView(card);
        }
        if (hasMore) {
            content.addView(ui.secondaryButton("Load more log entries", v -> {
                if (problems) problemsLimit += LOG_PAGE_SIZE;
                else timelineLimit += LOG_PAGE_SIZE;
                rebuild();
            }));
        }
    }

    private void raw(LinearLayout content, RunRecord run) {
        LinearLayout loading = ui.surface();
        loading.addView(ui.text("Loading raw output...", UiTypography.Role.CARD_TITLE, ui.palette.text));
        loading.addView(ui.text("Only the newest bounded indexed output is loaded into this view.",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        content.addView(loading);
        int generation = ++loadGeneration;
        logExecutor.execute(() -> {
            List<DevtoolDatabase.LogLine> lines = DevtoolDatabase.get(this).rawLogLines(run.runId, RAW_LINE_LIMIT, 0);
            mainHandler.post(() -> {
                if (generation != loadGeneration || isFinishing()) return;
                content.removeAllViews();
                renderRaw(content, run, lines);
            });
        });
    }

    private void renderRaw(LinearLayout content, RunRecord run, List<DevtoolDatabase.LogLine> lines) {
        LinearLayout card = ui.surface();
        StringBuilder indexed = new StringBuilder();
        Collections.reverse(lines);
        for (DevtoolDatabase.LogLine line : lines) {
            String value = line.raw.isEmpty() ? line.message : line.raw;
            if (value.isEmpty()) continue;
            if (indexed.length() > 0) indexed.append('\n');
            indexed.append(value);
            if (indexed.length() > RAW_TEXT_LIMIT) {
                indexed.delete(0, indexed.length() - RAW_TEXT_LIMIT);
                indexed.insert(0, "… older indexed output omitted from this view …\n");
                break;
            }
        }
        String output = join("\n\n", run.launchLog, run.summary, run.detail, run.error, run.stderr, run.stdout, indexed.toString());
        TextView raw = ui.text(output.isEmpty() ? "No raw output was captured." : output,
                UiTypography.Role.BODY_SMALL, output.isEmpty() ? ui.palette.muted : ui.palette.text);
        raw.setTypeface(Typeface.MONOSPACE);
        raw.setTextIsSelectable(true);
        raw.setHorizontallyScrolling(false);
        card.addView(raw);
        content.addView(card);
    }

    private void metadata(LinearLayout content, RunRecord run) {
        addMetadata(content, "Run ID", run.runId);
        addMetadata(content, "Retention", run.pinned ? "Pinned; automatic cleanup will preserve this run"
                : "Standard retention policy");
        addMetadata(content, "Repository", run.repo);
        addMetadata(content, "Repository identity", run.repositoryId);
        addMetadata(content, "Working directory", run.workdir);
        addMetadata(content, "Target", run.target);
        addMetadata(content, "Project", run.projectType);
        addMetadata(content, "Environment", run.executionEnvironment);
        addMetadata(content, "Command", run.command);
        addMetadata(content, "Artifact", run.artifact);
        addMetadata(content, "Commit", run.commitHash);
        addMetadata(content, "Transaction backup", run.transactionBackup);
        addMetadata(content, "Authoritative log", run.logPath);
        addMetadata(content, "Log source", run.logSourceId);
        addMetadata(content, "Exit code", String.valueOf(run.exitCode));
        addMetadata(content, "Started", DateFormat.getDateTimeInstance().format(new Date(run.startedAtMs)));
        addMetadata(content, "Updated", DateFormat.getDateTimeInstance().format(new Date(run.updatedAtMs)));
        addMetadata(content, "Duration", duration(run.durationMs));
        RepositoryOperationRecord operation = DevtoolDatabase.get(this).repositoryOperationForRun(run.runId);
        if (operation != null) {
            addMetadata(content, "Git operation", operation.type.replace('_', ' '));
            addMetadata(content, "Operation state", operation.state);
            addMetadata(content, "Operation ID", operation.operationId);
        }
        if (!run.repositoryId.isEmpty()) {
            content.addView(ui.button(operation == null ? "Open repository" : "Open repository Sync queue",
                    ui.palette.cyan, v -> startActivity(
                    new Intent(this, RepositoryDetailActivity.class)
                            .putExtra(RepositoryDetailActivity.EXTRA_REPOSITORY_ID, run.repositoryId)
                            .putExtra(RepositoryDetailActivity.EXTRA_TAB, operation == null ? 0 : 4))));
            content.addView(ui.secondaryButton("Show all logs for this repository", v -> startActivity(
                    new Intent(this, MainActivity.class)
                            .putExtra("open_page", 2)
                            .putExtra("logs_repository_id", run.repositoryId)
                            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP))));
        }
    }

    private void addMetadata(LinearLayout content, String label, String value) {
        if (value == null || value.trim().isEmpty()) return;
        LinearLayout card = ui.surface();
        card.addView(ui.text(label, UiTypography.Role.LABEL, ui.palette.muted));
        TextView text = ui.text(value, UiTypography.Role.BODY_SMALL, ui.palette.text);
        text.setTextIsSelectable(true);
        if (label.equals("Command") || label.contains("path") || label.contains("Repository")) text.setTypeface(Typeface.MONOSPACE);
        card.addView(text);
        content.addView(card);
    }

    private int statusColor(String status) {
        if (status.equals("ok")) return ui.palette.green;
        if (status.equals("failed")) return ui.palette.red;
        if (status.equals("cancelled") || status.equals("interrupted")) return ui.palette.yellow;
        return ui.palette.cyan;
    }

    private int severityColor(String severity) {
        if (severity.equals("error") || severity.equals("fatal")) return ui.palette.red;
        if (severity.equals("warning")) return ui.palette.yellow;
        if (severity.equals("success")) return ui.palette.green;
        return ui.palette.cyan;
    }

    private static String resolveRunId(Intent intent) {
        if (intent == null) return "";
        String extra = intent.getStringExtra(EXTRA_RUN_ID);
        if (extra != null && !extra.trim().isEmpty()) return extra.trim();
        Uri data = intent.getData();
        if (data != null && "devtool".equals(data.getScheme()) && "logs".equals(data.getHost())) {
            List<String> segments = data.getPathSegments();
            if (segments.size() >= 2 && segments.get(0).equals("run")) return segments.get(1);
        }
        return "";
    }

    private static String duration(long value) {
        long seconds = Math.max(0L, value / 1000L);
        if (seconds < 60) return seconds + "s";
        long minutes = seconds / 60;
        long remaining = seconds % 60;
        return minutes + "m " + remaining + "s";
    }

    private static String time(long value) {
        return new java.text.SimpleDateFormat("HH:mm:ss", Locale.ROOT).format(new Date(value));
    }

    private static String join(String separator, String... values) {
        StringBuilder result = new StringBuilder();
        for (String value : values) {
            if (value == null || value.trim().isEmpty()) continue;
            if (result.length() > 0) result.append(separator);
            result.append(value.trim());
        }
        return result.toString();
    }
}
