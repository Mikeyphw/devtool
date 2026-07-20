package io.github.mikeceead.devtoolhud;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.WindowInsets;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.List;

public final class GitCommitActivity extends Activity {
    static final String EXTRA_REPOSITORY_ID = "repository_id";
    static final String EXTRA_HASH = "commit_hash";

    private final Handler main = new Handler(Looper.getMainLooper());
    private UiKit ui;
    private LinearLayout content;
    private RepositoryRecord repository;
    private String hash = "";

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        ui = new UiKit(this);
        getWindow().setStatusBarColor(ui.palette.background);
        getWindow().setNavigationBarColor(ui.palette.background);
        hash = clean(getIntent().getStringExtra(EXTRA_HASH));
        repository = RepositoryStore.find(this, clean(getIntent().getStringExtra(EXTRA_REPOSITORY_ID)));
        setContentView(build());
        load();
    }

    private View build() {
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
        header.setPadding(ui.dp(18), ui.dp(14), ui.dp(18), ui.dp(8));
        header.addView(ui.text("Commit details", UiTypography.Role.PAGE_TITLE, ui.palette.text));
        TextView hashView = ui.text(hash, UiTypography.Role.BODY_SMALL, ui.palette.cyan);
        hashView.setTypeface(Typeface.MONOSPACE);
        hashView.setTextIsSelectable(true);
        header.addView(hashView);
        header.addView(ui.secondaryButton("Copy hash", v -> copy("Commit hash", hash)));
        if (repository != null && remoteCommitUrl(repository.remote, hash) != null) {
            header.addView(ui.secondaryButton("Open remote commit", v -> startActivity(
                    new Intent(Intent.ACTION_VIEW, remoteCommitUrl(repository.remote, hash)))));
        }
        root.addView(header);
        ScrollView scroll = new ScrollView(this);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(ui.dp(16), 0, ui.dp(16), ui.dp(24));
        content.addView(ui.text("Loading commit…", UiTypography.Role.BODY, ui.palette.muted));
        scroll.addView(content);
        root.addView(scroll, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        return root;
    }

    private void load() {
        if (repository == null) {
            showError("Repository unavailable.");
            return;
        }
        new Thread(() -> {
            GitCommitDetail detail = new RootCommandBroker(this).commitDetail(repository.location(), hash);
            List<RunRecord> runs = DevtoolDatabase.get(this)
                    .runsForCommit(repository.repositoryId, hash);
            main.post(() -> render(detail, runs));
        }, "devtool-git-commit").start();
    }

    private void render(GitCommitDetail detail, List<RunRecord> runs) {
        content.removeAllViews();
        if (!detail.success()) {
            showError(detail.error);
            return;
        }
        LinearLayout detailCard = ui.surface();
        TextView body = ui.text(detail.text, UiTypography.Role.BODY_SMALL, ui.palette.text);
        body.setTypeface(Typeface.MONOSPACE);
        body.setTextIsSelectable(true);
        body.setHorizontallyScrolling(true);
        detailCard.addView(body);
        detailCard.addView(ui.secondaryButton("Copy details", v -> copy("Commit details", detail.text)));
        content.addView(detailCard);

        content.addView(ui.sectionTitle("Associated Devtool runs"));
        if (runs.isEmpty()) {
            LinearLayout empty = ui.surface();
            empty.addView(ui.text("No correlated run", UiTypography.Role.CARD_TITLE, ui.palette.text));
            empty.addView(ui.text("No indexed Devtool run records this commit hash yet.",
                    UiTypography.Role.BODY_SMALL, ui.palette.muted));
            content.addView(empty);
            return;
        }
        for (RunRecord run : runs) {
            LinearLayout card = ui.surface();
            card.addView(ui.text(run.title, UiTypography.Role.CARD_TITLE, ui.palette.text));
            card.addView(ui.text(run.status.toUpperCase(java.util.Locale.ROOT) + " · " + run.summary,
                    UiTypography.Role.BODY_SMALL,
                    run.status.equals("failed") ? ui.palette.red : ui.palette.green));
            card.addView(ui.button("Open run log", ui.palette.cyan, v -> startActivity(
                    new Intent(this, LogViewerActivity.class)
                            .putExtra(LogViewerActivity.EXTRA_RUN_ID, run.runId))));
            if (!run.artifact.isEmpty()) card.addView(ui.secondaryButton("Copy artifact path",
                    v -> copy("Artifact", run.artifact)));
            content.addView(card);
        }
    }

    private void showError(String message) {
        if (content == null) return;
        content.removeAllViews();
        LinearLayout card = ui.surface();
        card.addView(ui.text("Unable to load commit", UiTypography.Role.CARD_TITLE, ui.palette.red));
        card.addView(ui.text(message, UiTypography.Role.BODY_SMALL, ui.palette.text));
        content.addView(card);
    }

    private void copy(String label, String value) {
        ClipboardManager manager = getSystemService(ClipboardManager.class);
        if (manager != null) manager.setPrimaryClip(ClipData.newPlainText(label, value));
        Toast.makeText(this, label + " copied", Toast.LENGTH_SHORT).show();
    }

    static Uri remoteCommitUrl(String remote, String hash) {
        String value = clean(remote);
        String commit = clean(hash);
        if (!commit.matches("[0-9a-fA-F]{7,64}")) return null;
        if (value.startsWith("git@")) {
            int colon = value.indexOf(':');
            if (colon > 4) value = "https://" + value.substring(4, colon) + "/" + value.substring(colon + 1);
        }
        if (value.startsWith("ssh://git@")) value = "https://" + value.substring(10);
        if (value.endsWith(".git")) value = value.substring(0, value.length() - 4);
        if (!value.startsWith("https://") && !value.startsWith("http://")) return null;
        String lower = value.toLowerCase(java.util.Locale.ROOT);
        String separator = lower.contains("gitlab") ? "/-/commit/"
                : lower.contains("bitbucket") ? "/commits/" : "/commit/";
        return Uri.parse(value + separator + commit);
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
