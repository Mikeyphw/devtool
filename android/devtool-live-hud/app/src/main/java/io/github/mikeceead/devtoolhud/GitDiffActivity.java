package io.github.mikeceead.devtoolhud;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.WindowInsets;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

public final class GitDiffActivity extends Activity {
    static final String EXTRA_REPOSITORY_ID = "repository_id";
    static final String EXTRA_PATH = "path";
    static final String EXTRA_STAGED = "staged";
    static final String EXTRA_UNTRACKED = "untracked";

    private final Handler main = new Handler(Looper.getMainLooper());
    private UiKit ui;
    private TextView output;
    private LinearLayout viewerHost;
    private String diffText = "";
    private String path = "";
    private boolean wrapLines;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        ui = new UiKit(this);
        getWindow().setStatusBarColor(ui.palette.background);
        getWindow().setNavigationBarColor(ui.palette.background);
        path = clean(getIntent().getStringExtra(EXTRA_PATH));
        boolean staged = getIntent().getBooleanExtra(EXTRA_STAGED, false);
        boolean untracked = getIntent().getBooleanExtra(EXTRA_UNTRACKED, false);
        String repositoryId = clean(getIntent().getStringExtra(EXTRA_REPOSITORY_ID));
        setContentView(build(staged, untracked));
        load(repositoryId, staged, untracked);
    }

    private View build(boolean staged, boolean untracked) {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(ui.palette.background);
        root.setPadding(ui.dp(16), ui.dp(12), ui.dp(16), ui.dp(16));
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                view.setPadding(ui.dp(16), bars.top + ui.dp(12), ui.dp(16), bars.bottom + ui.dp(16));
            }
            return insets;
        });
        root.addView(ui.text(staged ? "Staged diff" : untracked ? "Untracked file" : "Working-tree diff",
                UiTypography.Role.PAGE_TITLE, ui.palette.text));
        TextView pathView = ui.text(path, UiTypography.Role.BODY_SMALL, ui.palette.muted);
        pathView.setTypeface(android.graphics.Typeface.MONOSPACE);
        pathView.setTextIsSelectable(true);
        root.addView(pathView);

        LinearLayout controls = new LinearLayout(this);
        controls.setOrientation(LinearLayout.HORIZONTAL);
        Switch wrap = ui.toggle("Wrap lines", false);
        wrap.setOnCheckedChangeListener((button, checked) -> {
            wrapLines = checked;
            renderViewer();
        });
        controls.addView(wrap, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        controls.addView(ui.secondaryButton("Copy", v -> copy()),
                new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        root.addView(controls);

        viewerHost = new LinearLayout(this);
        viewerHost.setOrientation(LinearLayout.VERTICAL);
        root.addView(viewerHost, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        diffText = "Loading diff…";
        renderViewer();
        return root;
    }

    private void renderViewer() {
        if (viewerHost == null) return;
        viewerHost.removeAllViews();
        output = ui.text(diffText, UiTypography.Role.BODY_SMALL, ui.palette.text);
        output.setTypeface(android.graphics.Typeface.MONOSPACE);
        output.setTextIsSelectable(true);
        output.setHorizontallyScrolling(!wrapLines);
        output.setPadding(ui.dp(12), ui.dp(12), ui.dp(12), ui.dp(24));
        output.setBackground(ui.flat(ui.palette.surface, 12));
        ScrollView vertical = new ScrollView(this);
        if (wrapLines) {
            vertical.addView(output, new ScrollView.LayoutParams(
                    ScrollView.LayoutParams.MATCH_PARENT, ScrollView.LayoutParams.WRAP_CONTENT));
        } else {
            HorizontalScrollView horizontal = new HorizontalScrollView(this);
            horizontal.setFillViewport(true);
            horizontal.addView(output, new HorizontalScrollView.LayoutParams(
                    HorizontalScrollView.LayoutParams.WRAP_CONTENT, HorizontalScrollView.LayoutParams.WRAP_CONTENT));
            vertical.addView(horizontal, new ScrollView.LayoutParams(
                    ScrollView.LayoutParams.MATCH_PARENT, ScrollView.LayoutParams.WRAP_CONTENT));
        }
        viewerHost.addView(vertical, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
    }

    private void load(String repositoryId, boolean staged, boolean untracked) {
        RepositoryRecord repository = RepositoryStore.find(this, repositoryId);
        if (repository == null) {
            diffText = "Repository unavailable.";
            renderViewer();
            return;
        }
        new Thread(() -> {
            RootCommandBroker.CommandResult result = new RootCommandBroker(this)
                    .diff(repository.location(), path, staged, untracked);
            String text;
            if (result.timedOut) text = "Diff generation timed out.";
            else if (result.exitCode != 0) text = compact(result.text(), "Unable to generate diff.");
            else if (result.text().isEmpty()) text = "No differences for this view.";
            else text = result.text() + (result.truncated ? "\n\n[Output truncated at 2 MiB]" : "");
            main.post(() -> {
                diffText = text;
                renderViewer();
            });
        }, "devtool-git-diff").start();
    }

    private void copy() {
        ClipboardManager manager = getSystemService(ClipboardManager.class);
        if (manager != null) manager.setPrimaryClip(ClipData.newPlainText("Git diff", diffText));
        Toast.makeText(this, "Diff copied", Toast.LENGTH_SHORT).show();
    }

    private static String compact(String value, String fallback) {
        String text = clean(value);
        if (text.isEmpty()) text = fallback;
        return text.length() > 4000 ? text.substring(0, 3997) + "…" : text;
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
