package io.github.mikeceead.devtoolhud;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.view.View;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.EditText;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.text.DateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.Map;

public final class RepositoryDetailActivity extends Activity {
    static final String EXTRA_REPOSITORY_ID = "repository_id";
    static final String EXTRA_TAB = "repository_tab";
    private UiKit ui;
    private String repositoryId = "";
    private int tab;
    private boolean registered;
    private long lastWorkspaceRefresh;
    private final Handler queueTickerHandler = new Handler(Looper.getMainLooper());
    private final Runnable queueTicker = new Runnable() {
        @Override public void run() {
            if (!registered || tab != 4 || !RepositoryWorkspaceStore.busy(repositoryId)) return;
            rebuild();
            queueTickerHandler.postDelayed(this, 1000L);
        }
    };

    private final BroadcastReceiver updates = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (RepositoryWorkspaceStore.ACTION_UPDATED.equals(intent.getAction())) {
                String id = intent.getStringExtra(RepositoryWorkspaceStore.EXTRA_REPOSITORY_ID);
                if (id != null && !id.isEmpty() && !id.equals(repositoryId)) return;
                String message = intent.getStringExtra(RepositoryWorkspaceStore.EXTRA_MESSAGE);
                if (message != null && !message.isEmpty() && !message.endsWith("…")) {
                    Toast.makeText(RepositoryDetailActivity.this, message, Toast.LENGTH_SHORT).show();
                }
            }
            rebuild();
        }
    };

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        repositoryId = getIntent().getStringExtra(EXTRA_REPOSITORY_ID);
        if (repositoryId == null) repositoryId = "";
        tab = Math.max(0, Math.min(7, getIntent().getIntExtra(EXTRA_TAB, 0)));
        RepositoryWorkspaceStore.initialize(this);
        rebuild();
        refreshWorkspace(false);
    }

    @Override @SuppressLint("UnspecifiedRegisterReceiverFlag") protected void onStart() {
        super.onStart();
        if (!registered) {
            IntentFilter filter = new IntentFilter(RepositoryStore.ACTION_UPDATED);
            filter.addAction(RunStore.ACTION_UPDATED);
            filter.addAction(RepositoryWorkspaceStore.ACTION_UPDATED);
            filter.addAction(RepositoryDoctor.ACTION_UPDATED);
            if (Build.VERSION.SDK_INT >= 33) registerReceiver(updates, filter, Context.RECEIVER_NOT_EXPORTED);
            else registerReceiver(updates, filter);
            registered = true;
        }
        scheduleQueueTicker();
    }

    @Override protected void onResume() {
        super.onResume();
        RepositoryWorkspaceStore.Snapshot snapshot = RepositoryWorkspaceStore.snapshot(repositoryId);
        if (snapshot.updatedAt == 0 || System.currentTimeMillis() - snapshot.updatedAt > 30_000L) {
            refreshWorkspace(false);
        }
    }

    @Override protected void onStop() {
        queueTickerHandler.removeCallbacks(queueTicker);
        if (registered) {
            unregisterReceiver(updates);
            registered = false;
        }
        super.onStop();
    }

    private void rebuild() {
        ui = new UiKit(this);
        getWindow().setStatusBarColor(ui.palette.background);
        getWindow().setNavigationBarColor(ui.palette.background);
        setContentView(build(RepositoryStore.find(this, repositoryId)));
    }

    private View build(RepositoryRecord repository) {
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
        if (repository == null) {
            LinearLayout missing = ui.surface();
            missing.addView(ui.text("Repository unavailable", UiTypography.Role.PAGE_TITLE, ui.palette.text));
            missing.addView(ui.text("This repository may have been removed from the saved list.",
                    UiTypography.Role.BODY, ui.palette.muted));
            root.addView(missing);
            return root;
        }

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(ui.dp(18), ui.dp(16), ui.dp(18), ui.dp(10));
        LinearLayout heading = new LinearLayout(this);
        heading.setOrientation(ui.verticalActions() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        TextView repositoryTitle = ui.text(repository.name(), UiTypography.Role.PAGE_TITLE, ui.palette.text);
        if (ui.verticalActions()) heading.addView(repositoryTitle);
        else heading.addView(repositoryTitle, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        heading.addView(ui.text(repository.status.toUpperCase(Locale.ROOT), UiTypography.Role.CAPTION,
                statusColor(repository.status)));
        header.addView(heading);
        TextView path = ui.text(repository.canonicalPath, UiTypography.Role.BODY_SMALL, ui.palette.muted);
        path.setTypeface(Typeface.MONOSPACE);
        path.setTextIsSelectable(true);
        header.addView(path);
        header.addView(ui.text(join(" · ", repository.branch, repository.location().displayEnvironment(),
                        repository.linuxUser, repository.activeRunCount > 0 ? repository.activeRunCount + " live" : ""),
                UiTypography.Role.LABEL, ui.palette.muted));
        root.addView(header);

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(ui.verticalActions() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        actions.setPadding(ui.dp(12), 0, ui.dp(12), ui.dp(8));
        Button use = ui.button("Use in Run", ui.palette.green, v -> useInRun(repository));
        Button refresh = ui.secondaryButton("Refresh", v -> {
            RepositoryStore.refreshAsync(this, false);
            refreshWorkspace(true);
        });
        Button save = ui.secondaryButton(repository.saved ? "Remove" : "Save", v -> toggleSaved(repository));
        if (ui.verticalActions()) {
            actions.addView(use); actions.addView(refresh); actions.addView(save);
        } else {
            actions.addView(use, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.2f));
            actions.addView(refresh, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            actions.addView(save, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        }
        root.addView(actions);

        LinearLayout tabs = new LinearLayout(this);
        tabs.setOrientation(ui.twoPaneLayout() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        String[] labels = {"Overview", "Changes", "Commits", "Branches", "Sync", "Activity", "Runs", "Metadata"};
        for (int i = 0; i < labels.length; i++) {
            int selected = i;
            Button button = ui.navButton(R.drawable.ic_repository, labels[i], tab == i, v -> {
                tab = selected;
                rebuild();
                scheduleQueueTicker();
                if ((tab == 1 || tab == 2 || tab == 3 || tab == 4)
                        && RepositoryWorkspaceStore.snapshot(repositoryId).updatedAt == 0) {
                    refreshWorkspace(false);
                }
            });
            if (ui.twoPaneLayout()) {
                button.setGravity(android.view.Gravity.START | android.view.Gravity.CENTER_VERTICAL);
                button.setCompoundDrawablesWithIntrinsicBounds(R.drawable.ic_repository, 0, 0, 0);
                button.setCompoundDrawablePadding(ui.dp(10));
                button.setPadding(ui.dp(12), ui.dp(10), ui.dp(10), ui.dp(10));
                tabs.addView(button, new LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
            } else {
                button.setMinWidth(ui.dp(104));
                tabs.addView(button, new LinearLayout.LayoutParams(ui.dp(112), LinearLayout.LayoutParams.WRAP_CONTENT));
            }
        }

        ScrollView scroll = new ScrollView(this);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(ui.contentHorizontalPadding(), ui.dp(2), ui.contentHorizontalPadding(), ui.dp(26));
        if (tab == 0) overview(content, repository);
        else if (tab == 1) changes(content, repository);
        else if (tab == 2) commits(content, repository);
        else if (tab == 3) branches(content, repository);
        else if (tab == 4) sync(content, repository);
        else if (tab == 5) activity(content, repository);
        else if (tab == 6) runs(content, repository);
        else metadata(content, repository);
        scroll.addView(content);

        if (ui.twoPaneLayout()) {
            tabs.setPadding(ui.dp(8), ui.dp(8), ui.dp(8), ui.dp(8));
            tabs.setBackgroundColor(ui.palette.surface);
            LinearLayout body = new LinearLayout(this);
            body.setOrientation(LinearLayout.HORIZONTAL);
            body.addView(tabs, new LinearLayout.LayoutParams(
                    ui.dp(AdaptiveLayout.detailRailWidthDp(this)), LinearLayout.LayoutParams.MATCH_PARENT));
            body.addView(scroll, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1f));
            root.addView(body, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        } else {
            HorizontalScrollView tabScroller = new HorizontalScrollView(this);
            tabScroller.setHorizontalScrollBarEnabled(false);
            tabScroller.addView(tabs);
            root.addView(tabScroller);
            root.addView(scroll, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        }
        return root;
    }

    private void overview(LinearLayout content, RepositoryRecord repository) {
        RepositoryWorkspaceStore.Snapshot snapshot = RepositoryWorkspaceStore.snapshot(repository.repositoryId);
        LinearLayout state = ui.surface();
        state.addView(ui.text(repository.status.equals("clean") ? "Working tree clean"
                        : repository.status.equals("modified") ? "Working tree modified"
                        : repository.status.equals("checking") ? "Repository has not been inspected yet"
                        : "Repository needs attention",
                UiTypography.Role.CARD_TITLE, statusColor(repository.status)));
        state.addView(ui.text(join(" · ", repository.branch.isEmpty() ? "No branch" : repository.branch,
                        repository.projectType, aheadBehind(repository)),
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        if (snapshot.updatedAt > 0 && snapshot.tree.success()) {
            state.addView(ui.text(snapshot.tree.changes.size() + " changed · "
                            + snapshot.tree.stagedCount() + " staged · "
                            + snapshot.tree.conflictedCount() + " conflicts",
                    UiTypography.Role.BODY_SMALL, ui.palette.text));
        }
        if (!repository.lastError.isEmpty()) state.addView(ui.text(repository.lastError,
                UiTypography.Role.BODY_SMALL, ui.palette.red));
        content.addView(state);

        content.addView(repositoryDoctorCard(repository));
        content.addView(repositoryPathResolutionCard(repository));

        addField(content, "Remote", repository.remote);
        addField(content, "Upstream", repository.upstream);
        addField(content, "Git root", repository.gitRoot);

        LinearLayout tools = ui.surface();
        tools.addView(ui.sectionTitle("Repository tools"));
        tools.addView(ui.button("Open shell here", ui.palette.cyan, v -> openShell(repository)));
        tools.addView(ui.secondaryButton("Browse files as root", v -> browse(repository)));
        tools.addView(ui.secondaryButton("Copy path", v -> copy("Repository path", repository.canonicalPath)));
        tools.addView(ui.secondaryButton("Run Doctor", v -> runDoctor(repository)));
        tools.addView(ui.secondaryButton("View this repository in Logs", v -> openRepositoryLogs(repository)));
        if (!repository.remote.isEmpty() && remoteUrl(repository.remote) != null) {
            tools.addView(ui.secondaryButton("Open remote", v -> startActivity(
                    new Intent(Intent.ACTION_VIEW, remoteUrl(repository.remote)))));
        }
        content.addView(tools);

        LinearLayout sync = ui.surface();
        sync.addView(ui.sectionTitle("Remote synchronization"));
        sync.addView(ui.text(repository.upstream.isEmpty()
                        ? "This branch has no upstream. The first push will set one using origin or the first configured remote."
                        : "Upstream " + repository.upstream + (aheadBehind(repository).isEmpty() ? "" : " · " + aheadBehind(repository)),
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        Button fetch = ui.button("Fetch all + prune", ui.palette.cyan,
                v -> RepositoryWorkspaceStore.fetchAll(this, repository));
        fetch.setEnabled(!RepositoryWorkspaceStore.busy(repository.repositoryId) && !repository.remote.isEmpty());
        sync.addView(fetch);
        Button pull = ui.secondaryButton("Pull · fast-forward only", v -> confirmPull(repository));
        pull.setEnabled(!RepositoryWorkspaceStore.busy(repository.repositoryId)
                && !repository.upstream.isEmpty() && !repository.branch.equals("detached"));
        sync.addView(pull);
        Button push = ui.secondaryButton(repository.upstream.isEmpty() ? "Push + set upstream" : "Push",
                v -> confirmPush(repository));
        push.setEnabled(!RepositoryWorkspaceStore.busy(repository.repositoryId)
                && !repository.remote.isEmpty() && !repository.branch.equals("detached"));
        sync.addView(push);
        content.addView(sync);
    }

    private void changes(LinearLayout content, RepositoryRecord repository) {
        RepositoryWorkspaceStore.Snapshot snapshot = RepositoryWorkspaceStore.snapshot(repository.repositoryId);
        if (snapshot.loading && snapshot.updatedAt == 0) {
            empty(content, "Inspecting working tree", "Reading porcelain-v2 status as "
                    + repository.location().displayEnvironment() + "…");
            return;
        }
        if (!snapshot.tree.success()) {
            empty(content, "Unable to read working tree", snapshot.tree.error.isEmpty()
                    ? "Refresh the repository to try again." : snapshot.tree.error);
            content.addView(ui.button("Retry", ui.palette.cyan, v -> refreshWorkspace(true)));
            return;
        }

        LinearLayout summary = ui.surface();
        summary.addView(ui.text(snapshot.tree.dirty() ? "Working tree changes" : "Working tree clean",
                UiTypography.Role.CARD_TITLE, snapshot.tree.dirty() ? ui.palette.yellow : ui.palette.green));
        summary.addView(ui.text(snapshot.tree.changes.size() + " files · "
                        + snapshot.tree.stagedCount() + " staged · "
                        + snapshot.tree.unstagedCount() + " unstaged · "
                        + snapshot.tree.conflictedCount() + " conflicted",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        if (!snapshot.message.isEmpty()) summary.addView(ui.text(snapshot.message,
                UiTypography.Role.BODY_SMALL, snapshot.message.toLowerCase(Locale.ROOT).contains("unable")
                        ? ui.palette.red : ui.palette.cyan));
        Button stageAll = ui.button("Stage all", ui.palette.green,
                v -> RepositoryWorkspaceStore.stageAll(this, repository));
        stageAll.setEnabled(snapshot.tree.dirty() && !RepositoryWorkspaceStore.busy(repository.repositoryId));
        summary.addView(stageAll);
        Button unstageAll = ui.secondaryButton("Unstage all",
                v -> RepositoryWorkspaceStore.unstageAll(this, repository));
        unstageAll.setEnabled(snapshot.tree.stagedCount() > 0 && !RepositoryWorkspaceStore.busy(repository.repositoryId));
        summary.addView(unstageAll);
        Button commit = ui.button("Commit staged changes", ui.palette.cyan,
                v -> showCommitDialog(repository));
        commit.setEnabled(snapshot.tree.stagedCount() > 0 && snapshot.tree.conflictedCount() == 0
                && !RepositoryWorkspaceStore.busy(repository.repositoryId));
        summary.addView(commit);
        Button stash = ui.secondaryButton("Stash including untracked", v -> showStashDialog(repository));
        stash.setEnabled(snapshot.tree.dirty() && !RepositoryWorkspaceStore.busy(repository.repositoryId));
        summary.addView(stash);
        if (!snapshot.stashes.isEmpty()) {
            GitStashRecord latest = snapshot.stashes.get(0);
            Button pop = ui.secondaryButton("Pop latest stash · " + latest.reference,
                    v -> confirmPopStash(repository, latest));
            pop.setEnabled(!RepositoryWorkspaceStore.busy(repository.repositoryId));
            summary.addView(pop);
        }
        Button reset = ui.secondaryButton("Hard reset tracked changes", v -> confirmHardReset(repository));
        reset.setEnabled(!snapshot.tree.headOid.isEmpty() && hasTrackedChanges(snapshot.tree)
                && !RepositoryWorkspaceStore.busy(repository.repositoryId));
        summary.addView(reset);
        content.addView(summary);

        if (snapshot.tree.changes.isEmpty()) return;
        for (Map.Entry<String, List<GitWorkingTree.GitChange>> group : snapshot.tree.grouped().entrySet()) {
            content.addView(ui.sectionTitle(group.getKey() + " · " + group.getValue().size()));
            for (GitWorkingTree.GitChange change : group.getValue()) {
                content.addView(changeCard(repository, change));
            }
        }
    }

    private View changeCard(RepositoryRecord repository, GitWorkingTree.GitChange change) {
        LinearLayout card = ui.surface();
        LinearLayout heading = new LinearLayout(this);
        heading.setOrientation(LinearLayout.HORIZONTAL);
        TextView path = ui.text(change.displayPath(), UiTypography.Role.BODY_SMALL, ui.palette.text);
        path.setTypeface(Typeface.MONOSPACE);
        path.setTextIsSelectable(true);
        heading.addView(path, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        heading.addView(ui.text(change.statusLabel(), UiTypography.Role.CAPTION, change.conflicted()
                ? ui.palette.red : change.staged() ? ui.palette.green : ui.palette.yellow));
        card.addView(heading);
        card.addView(ui.text("Index " + change.indexStatus() + " · Worktree " + change.worktreeStatus()
                        + (change.submodule.equals("N...") ? "" : " · submodule " + change.submodule),
                UiTypography.Role.CAPTION, ui.palette.muted));

        if (change.hasWorkingDiff()) card.addView(ui.secondaryButton(change.untracked()
                ? "View untracked file" : "View working diff", v -> openDiff(repository, change, false)));
        if (change.hasStagedDiff()) card.addView(ui.secondaryButton("View staged diff",
                v -> openDiff(repository, change, true)));
        if (change.canStage()) {
            card.addView(ui.button("Stage", ui.palette.green,
                    v -> RepositoryWorkspaceStore.stage(this, repository, change.path)));
        }
        if (change.canUnstage()) {
            card.addView(ui.secondaryButton("Unstage",
                    v -> RepositoryWorkspaceStore.unstage(this, repository, change.path)));
        }
        if (change.canDiscardWorktree()) {
            card.addView(ui.secondaryButton("Discard working-tree changes",
                    v -> confirmDiscard(repository, change)));
        }
        card.addView(ui.secondaryButton("Copy path", v -> copy("File path", change.path)));
        return card;
    }

    private void commits(LinearLayout content, RepositoryRecord repository) {
        RepositoryWorkspaceStore.Snapshot snapshot = RepositoryWorkspaceStore.snapshot(repository.repositoryId);
        if (snapshot.loading && snapshot.updatedAt == 0) {
            empty(content, "Loading commits", "Reading the latest Git history…");
            return;
        }
        if (!snapshot.tree.success()) {
            empty(content, "Unable to read commits", snapshot.tree.error);
            return;
        }
        if (snapshot.commits.isEmpty()) {
            empty(content, "No commits", "This repository has no reachable commits yet.");
            return;
        }
        for (GitCommitRecord commit : snapshot.commits) {
            LinearLayout card = ui.surface();
            LinearLayout heading = new LinearLayout(this);
            heading.setOrientation(LinearLayout.HORIZONTAL);
            heading.addView(ui.text(commit.subject.isEmpty() ? "Untitled commit" : commit.subject,
                    UiTypography.Role.CARD_TITLE, ui.palette.text),
                    new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            heading.addView(ui.text(commit.head ? "HEAD" : commit.shortHash,
                    UiTypography.Role.CAPTION, commit.head ? ui.palette.green : ui.palette.cyan));
            card.addView(heading);
            TextView hash = ui.text(commit.fullHash, UiTypography.Role.CAPTION, ui.palette.muted);
            hash.setTypeface(Typeface.MONOSPACE);
            hash.setTextIsSelectable(true);
            card.addView(hash);
            card.addView(ui.text(join(" · ", commit.authorName, commit.authoredAt, commit.refs),
                    UiTypography.Role.BODY_SMALL, ui.palette.muted));

            List<RunRecord> associated = DevtoolDatabase.get(this)
                    .runsForCommit(repository.repositoryId, commit.fullHash);
            if (!associated.isEmpty()) {
                RunRecord run = associated.get(0);
                card.addView(ui.text(associated.size() + " correlated Devtool run"
                                + (associated.size() == 1 ? "" : "s")
                                + (run.artifact.isEmpty() ? "" : " · artifact available"),
                        UiTypography.Role.BODY_SMALL, ui.palette.green));
                card.addView(ui.secondaryButton("Open correlated run", v -> openLog(run.runId)));
            }
            card.addView(ui.button("Commit details", ui.palette.cyan,
                    v -> openCommit(repository, commit.fullHash)));
            Uri remote = GitCommitActivity.remoteCommitUrl(repository.remote, commit.fullHash);
            if (remote != null) card.addView(ui.secondaryButton("Open remote commit",
                    v -> startActivity(new Intent(Intent.ACTION_VIEW, remote))));
            card.addView(ui.secondaryButton("Copy hash", v -> copy("Commit hash", commit.fullHash)));
            content.addView(card);
        }
    }

    private void branches(LinearLayout content, RepositoryRecord repository) {
        RepositoryWorkspaceStore.Snapshot snapshot = RepositoryWorkspaceStore.snapshot(repository.repositoryId);
        if (snapshot.loading && snapshot.updatedAt == 0) {
            empty(content, "Loading branches", "Reading local branches, remote branches, and stashes…");
            return;
        }
        if (!snapshot.tree.success()) {
            empty(content, "Unable to read branches", snapshot.tree.error);
            return;
        }

        LinearLayout actions = ui.surface();
        actions.addView(ui.text(snapshot.tree.detached ? "Detached HEAD" : "Current branch · " + snapshot.tree.branch,
                UiTypography.Role.CARD_TITLE, snapshot.tree.detached ? ui.palette.yellow : ui.palette.green));
        actions.addView(ui.text(snapshot.tree.upstream.isEmpty() ? "No upstream configured"
                        : "Upstream " + snapshot.tree.upstream + (aheadBehind(repository).isEmpty() ? "" : " · " + aheadBehind(repository)),
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        Button create = ui.button("Create branch", ui.palette.cyan, v -> showCreateBranchDialog(repository));
        create.setEnabled(!RepositoryWorkspaceStore.busy(repository.repositoryId));
        actions.addView(create);
        Button fetch = ui.secondaryButton("Fetch all + prune",
                v -> RepositoryWorkspaceStore.fetchAll(this, repository));
        fetch.setEnabled(!RepositoryWorkspaceStore.busy(repository.repositoryId) && !repository.remote.isEmpty());
        actions.addView(fetch);
        content.addView(actions);

        int localCount = 0;
        for (GitBranchRecord branch : snapshot.branches) if (!branch.remote) localCount++;
        content.addView(ui.sectionTitle("Local branches · " + localCount));
        if (localCount == 0) empty(content, "No local branches", "Create the first branch after the repository has an initial commit.");
        for (GitBranchRecord branch : snapshot.branches) {
            if (branch.remote) continue;
            content.addView(branchCard(repository, branch));
        }

        int remoteCount = 0;
        for (GitBranchRecord branch : snapshot.branches) if (branch.remote) remoteCount++;
        content.addView(ui.sectionTitle("Remote branches · " + remoteCount));
        if (remoteCount == 0) empty(content, "No remote branches", "Fetch a configured remote to discover its branches.");
        for (GitBranchRecord branch : snapshot.branches) {
            if (!branch.remote) continue;
            content.addView(branchCard(repository, branch));
        }

        content.addView(ui.sectionTitle("Stashes · " + snapshot.stashes.size()));
        if (snapshot.stashes.isEmpty()) {
            empty(content, "No stashes", "Stashes created by Git or the Android app will appear here.");
        } else {
            for (GitStashRecord stash : snapshot.stashes) {
                LinearLayout card = ui.surface();
                LinearLayout heading = new LinearLayout(this);
                heading.setOrientation(LinearLayout.HORIZONTAL);
                heading.addView(ui.text(stash.reference, UiTypography.Role.CARD_TITLE, ui.palette.cyan),
                        new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
                heading.addView(ui.text(stash.createdAt, UiTypography.Role.CAPTION, ui.palette.muted));
                card.addView(heading);
                card.addView(ui.text(stash.subject.isEmpty() ? "Saved working tree" : stash.subject,
                        UiTypography.Role.BODY_SMALL, ui.palette.text));
                Button pop = ui.secondaryButton("Pop this stash", v -> confirmPopStash(repository, stash));
                pop.setEnabled(!RepositoryWorkspaceStore.busy(repository.repositoryId));
                card.addView(pop);
                card.addView(ui.secondaryButton("Copy stash ref", v -> copy("Stash reference", stash.reference)));
                content.addView(card);
            }
        }
    }

    private View branchCard(RepositoryRecord repository, GitBranchRecord branch) {
        LinearLayout card = ui.surface();
        LinearLayout heading = new LinearLayout(this);
        heading.setOrientation(LinearLayout.HORIZONTAL);
        TextView name = ui.text(branch.name, UiTypography.Role.CARD_TITLE, ui.palette.text);
        name.setTypeface(Typeface.MONOSPACE);
        heading.addView(name, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        heading.addView(ui.text(branch.current ? "CURRENT" : branch.remote ? "REMOTE" : "LOCAL",
                UiTypography.Role.CAPTION, branch.current ? ui.palette.green : branch.remote ? ui.palette.cyan : ui.palette.muted));
        card.addView(heading);
        String tracking = branch.trackingLabel();
        if (!tracking.isEmpty()) card.addView(ui.text(tracking, UiTypography.Role.BODY_SMALL,
                branch.upstreamGone ? ui.palette.red : ui.palette.muted));
        TextView oid = ui.text(branch.objectId, UiTypography.Role.CAPTION, ui.palette.muted);
        oid.setTypeface(Typeface.MONOSPACE);
        card.addView(oid);
        if (!branch.current) {
            Button checkout = ui.button(branch.remote ? "Checkout as tracking branch" : "Checkout",
                    ui.palette.cyan, v -> confirmCheckout(repository, branch));
            checkout.setEnabled(!RepositoryWorkspaceStore.busy(repository.repositoryId));
            card.addView(checkout);
        }
        card.addView(ui.secondaryButton("Copy branch", v -> copy("Branch", branch.name)));
        return card;
    }

    private void sync(LinearLayout content, RepositoryRecord repository) {
        RepositoryWorkspaceStore.Snapshot snapshot = RepositoryWorkspaceStore.snapshot(repository.repositoryId);
        content.addView(queueRecoveryCard(repository));

        content.addView(ui.sectionTitle("Operation queue"));
        List<RepositoryOperationRecord> operations = RepositoryWorkspaceStore.operations(this, repository.repositoryId);
        List<RepositoryOperationRecord> queuedOperations = new ArrayList<>();
        for (RepositoryOperationRecord operation : operations) {
            if (operation.state.equals("queued")) queuedOperations.add(operation);
        }
        boolean active = false;
        long now = System.currentTimeMillis();
        for (RepositoryOperationRecord operation : operations) {
            if (!operation.active()) continue;
            active = true;
            LinearLayout card = ui.surface();
            LinearLayout heading = new LinearLayout(this);
            heading.setOrientation(ui.verticalActions() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
            TextView operationTitle = ui.text(operation.type.replace('_', ' '),
                    UiTypography.Role.CARD_TITLE, ui.palette.text);
            if (ui.verticalActions()) heading.addView(operationTitle);
            else heading.addView(operationTitle, new LinearLayout.LayoutParams(
                    0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            heading.addView(ui.text(operation.state.toUpperCase(Locale.ROOT), UiTypography.Role.CAPTION,
                    operation.state.equals("running") ? ui.palette.cyan : ui.palette.yellow));
            card.addView(heading);
            card.addView(ui.text(operation.summary, UiTypography.Role.BODY_SMALL, ui.palette.muted));
            if (operation.state.equals("running")) {
                ProgressBar progress = new ProgressBar(this);
                progress.setIndeterminate(true);
                progress.setContentDescription(operation.progressLabel + ", running for "
                        + duration(operation.elapsedAt(now)));
                card.addView(progress);
                card.addView(ui.text(operation.progressLabel + " · " + duration(operation.elapsedAt(now)),
                        UiTypography.Role.CAPTION, ui.palette.cyan));
            } else if (operation.state.equals("queued")) {
                int queueIndex = queuedOperations.indexOf(operation);
                card.addView(ui.text("Queue position " + (queueIndex + 1) + " of " + queuedOperations.size()
                                + " · waiting " + duration(operation.elapsedAt(now)),
                        UiTypography.Role.CAPTION, ui.palette.yellow));
                LinearLayout orderActions = new LinearLayout(this);
                orderActions.setOrientation(ui.verticalActions() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
                Button earlier = ui.secondaryButton("Move earlier", v ->
                        RepositoryWorkspaceStore.moveQueued(this, repository.repositoryId, operation.operationId, -1));
                Button later = ui.secondaryButton("Move later", v ->
                        RepositoryWorkspaceStore.moveQueued(this, repository.repositoryId, operation.operationId, 1));
                earlier.setEnabled(queueIndex > 0);
                later.setEnabled(queueIndex + 1 < queuedOperations.size());
                if (ui.verticalActions()) {
                    orderActions.addView(earlier);
                    orderActions.addView(later);
                } else {
                    orderActions.addView(earlier, new LinearLayout.LayoutParams(0,
                            LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
                    orderActions.addView(later, new LinearLayout.LayoutParams(0,
                            LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
                }
                card.addView(orderActions);
            }
            if (!operation.target.isEmpty()) {
                TextView target = ui.text(operation.target, UiTypography.Role.CAPTION, ui.palette.muted);
                target.setTypeface(Typeface.MONOSPACE);
                target.setTextIsSelectable(true);
                card.addView(target);
            }
            if (operation.cancellable) card.addView(ui.secondaryButton(
                    operation.state.equals("running") ? "Cancel running operation"
                            : operation.state.equals("orphaned") ? "Terminate orphaned operation" : "Remove from queue",
                    v -> RepositoryWorkspaceStore.cancel(this, repository.repositoryId, operation.operationId)));
            if (RepoQueueRecovery.canTerminateOrphan(operation)) {
                card.addView(ui.secondaryButton("Terminate orphan with confirmation",
                        v -> confirmTerminateOrphan(repository, operation)));
            }
            if (RepoQueueRecovery.canMarkInterrupted(operation)) {
                card.addView(ui.secondaryButton("Mark interrupted", v -> confirmMarkInterrupted(repository, operation)));
            }
            card.addView(ui.text(RepoQueueRecovery.recoveryHint(operation), UiTypography.Role.CAPTION, ui.palette.muted));
            card.addView(ui.secondaryButton("Copy Git trace", v -> copy("Git trace",
                    GitOperationTrace.plainText(this, operation, ""))));
            card.addView(ui.secondaryButton("Open structured log", v -> openLog(operation.runId)));
            content.addView(card);
        }
        if (!active) empty(content, "Queue is idle",
                "Git operations run one at a time per repository. Queued operations can be reordered or cancelled safely.");

        content.addView(ui.sectionTitle("Incoming and outgoing commits"));
        GitSyncPreview preview = snapshot.syncPreview;
        if (!preview.available()) {
            empty(content, "No upstream preview", preview.error.isEmpty()
                    ? "Refresh after configuring an upstream branch." : preview.error);
        } else {
            LinearLayout summary = ui.surface();
            summary.addView(ui.text(preview.upstream, UiTypography.Role.CARD_TITLE, ui.palette.text));
            summary.addView(ui.text(preview.ahead + " outgoing · " + preview.behind + " incoming",
                    UiTypography.Role.BODY_SMALL, ui.palette.muted));
            content.addView(summary);
            addSyncCommits(content, repository, "Outgoing", preview.outgoing, ui.palette.green);
            addSyncCommits(content, repository, "Incoming", preview.incoming, ui.palette.cyan);
        }

        content.addView(ui.sectionTitle("Credential and SSH diagnostics"));
        RemoteDiagnostics diagnostics = snapshot.remoteDiagnostics;
        LinearLayout diagnostic = ui.surface();
        diagnostic.addView(ui.text(diagnostics.ready ? "Remote prerequisites ready" : "Remote setup needs attention",
                UiTypography.Role.CARD_TITLE, diagnostics.ready ? ui.palette.green : ui.palette.yellow));
        diagnostic.addView(ui.text(diagnostics.summary(), UiTypography.Role.BODY_SMALL, ui.palette.muted));
        if (!diagnostics.transport.equals("none")) diagnostic.addView(ui.text(
                join(" · ", diagnostics.transport.toUpperCase(Locale.ROOT), diagnostics.host,
                        diagnostics.sshAgentAvailable ? "SSH agent" : "",
                        diagnostics.knownHost ? "known host" : ""),
                UiTypography.Role.LABEL, ui.palette.muted));
        if (!diagnostics.credentialHelper.isEmpty()) diagnostic.addView(ui.text(
                "Credential helper: " + diagnostics.credentialHelper, UiTypography.Role.CAPTION, ui.palette.muted));
        if (!diagnostics.identityFiles.isEmpty()) diagnostic.addView(ui.text(
                diagnostics.identityFiles.size() + " readable SSH identity file(s) detected. Paths are not exported to logs.",
                UiTypography.Role.CAPTION, ui.palette.muted));
        diagnostic.addView(ui.text("This is a passive local check. It never contacts the remote or displays credentials.",
                UiTypography.Role.CAPTION, ui.palette.muted));
        if (!diagnostics.remediationSteps.isEmpty()) {
            diagnostic.addView(ui.sectionTitle("Suggested setup commands"));
            diagnostic.addView(ui.text("Review commands before running them. Host fingerprints must be verified independently.",
                    UiTypography.Role.CAPTION, ui.palette.yellow));
            for (String command : diagnostics.remediationSteps) {
                LinearLayout step = ui.surface();
                TextView commandView = ui.text(command, UiTypography.Role.CODE, ui.palette.text);
                commandView.setTypeface(Typeface.MONOSPACE);
                commandView.setTextIsSelectable(true);
                step.addView(commandView);
                step.addView(ui.secondaryButton("Copy command", v -> copy("Setup command", command)));
                diagnostic.addView(step);
            }
            diagnostic.addView(ui.secondaryButton("Open repository shell", v -> openShell(repository)));
        }
        content.addView(diagnostic);

        content.addView(ui.sectionTitle("Recent Git operations"));
        int shown = 0;
        for (RepositoryOperationRecord operation : operations) {
            if (operation.active()) continue;
            LinearLayout card = ui.surface();
            LinearLayout heading = new LinearLayout(this);
            heading.setOrientation(LinearLayout.HORIZONTAL);
            heading.addView(ui.text(operation.type.replace('_', ' '), UiTypography.Role.CARD_TITLE, ui.palette.text),
                    new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            int color = operation.state.equals("ok") ? ui.palette.green
                    : operation.state.equals("failed") ? ui.palette.red : ui.palette.yellow;
            heading.addView(ui.text(operation.state.toUpperCase(Locale.ROOT), UiTypography.Role.CAPTION, color));
            card.addView(heading);
            card.addView(ui.text(operation.summary, UiTypography.Role.BODY_SMALL, ui.palette.muted));
            card.addView(ui.text(RepoQueueRecovery.recoveryHint(operation), UiTypography.Role.CAPTION, ui.palette.muted));
            if (RepoQueueRecovery.canRetry(operation)) {
                card.addView(ui.secondaryButton("Retry with same execution plan", v -> confirmRetryOperation(repository, operation, false)));
                card.addView(ui.secondaryButton("Retry after path repair", v -> confirmRetryOperation(repository, operation, true)));
            }
            card.addView(ui.secondaryButton("Copy Git trace", v -> copy("Git trace",
                    GitOperationTrace.plainText(this, operation, ""))));
            card.addView(ui.secondaryButton("Open structured log", v -> openLog(operation.runId)));
            content.addView(card);
            if (++shown >= 20) break;
        }
        if (shown == 0) empty(content, "No completed Git operations", "Stage, commit, synchronize, or switch branches to create structured operation logs.");
    }

    private void addSyncCommits(LinearLayout content, RepositoryRecord repository, String label,
                                List<GitSyncCommit> commits, int color) {
        content.addView(ui.sectionTitle(label + " · " + commits.size()));
        if (commits.isEmpty()) {
            empty(content, "No " + label.toLowerCase(Locale.ROOT) + " commits", "The local upstream references are already aligned in this direction.");
            return;
        }
        for (GitSyncCommit commit : commits) {
            LinearLayout card = ui.surface();
            LinearLayout heading = new LinearLayout(this);
            heading.setOrientation(LinearLayout.HORIZONTAL);
            heading.addView(ui.text(commit.subject.isEmpty() ? "Commit " + commit.shortHash : commit.subject,
                    UiTypography.Role.CARD_TITLE, ui.palette.text),
                    new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            heading.addView(ui.text(commit.shortHash, UiTypography.Role.CAPTION, color));
            card.addView(heading);
            card.addView(ui.text(join(" · ", commit.author, commit.authoredAt), UiTypography.Role.CAPTION, ui.palette.muted));
            card.addView(ui.secondaryButton("Open commit", v -> openCommit(repository, commit.hash)));
            content.addView(card);
        }
    }

    private void activity(LinearLayout content, RepositoryRecord repository) {
        List<DevtoolDatabase.RepositoryEvent> events = DevtoolDatabase.get(this)
                .repositoryEvents(repository.repositoryId);
        if (events.isEmpty()) {
            empty(content, "No repository activity yet",
                    "Devtool runs and repository events will appear here.");
            return;
        }
        for (DevtoolDatabase.RepositoryEvent event : events) {
            LinearLayout card = ui.surface();
            LinearLayout heading = new LinearLayout(this);
            heading.setOrientation(LinearLayout.HORIZONTAL);
            heading.addView(ui.text(event.type.replace('_', ' '), UiTypography.Role.CAPTION,
                    event.type.contains("FAILED") ? ui.palette.red
                            : event.type.contains("SUCCEEDED") ? ui.palette.green : ui.palette.cyan));
            TextView time = ui.text(DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
                    .format(new Date(event.timestampMs)), UiTypography.Role.CAPTION, ui.palette.muted);
            time.setGravity(android.view.Gravity.END);
            heading.addView(time, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            card.addView(heading);
            card.addView(ui.text(event.summary, UiTypography.Role.BODY_SMALL, ui.palette.text));
            if (!event.runId.isEmpty()) card.addView(ui.secondaryButton("Open log", v -> openLog(event.runId)));
            content.addView(card);
        }
    }

    private void runs(LinearLayout content, RepositoryRecord repository) {
        List<RunRecord> values = RunStore.forRepository(this, repository);
        if (values.isEmpty()) {
            empty(content, "No recorded runs", "Runs using this repository will be linked automatically.");
            return;
        }
        for (RunRecord run : values) {
            LinearLayout card = ui.surface();
            LinearLayout heading = new LinearLayout(this);
            heading.setOrientation(LinearLayout.HORIZONTAL);
            heading.addView(ui.text(run.title, UiTypography.Role.CARD_TITLE, ui.palette.text),
                    new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            heading.addView(ui.text(run.status.toUpperCase(Locale.ROOT), UiTypography.Role.CAPTION,
                    run.status.equals("ok") ? ui.palette.green : run.status.equals("failed") ? ui.palette.red : ui.palette.cyan));
            card.addView(heading);
            card.addView(ui.text(join(" · ", run.phase, run.target, duration(run.durationMs),
                            DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(new Date(run.updatedAtMs))),
                    UiTypography.Role.LABEL, ui.palette.muted));
            card.addView(ui.text(run.summary.isEmpty() ? run.detail : run.summary,
                    UiTypography.Role.BODY_SMALL, ui.palette.text));
            card.addView(ui.button("Open log", ui.palette.cyan, v -> openLog(run.runId)));
            if (!run.artifact.isEmpty()) card.addView(ui.secondaryButton("Copy artifact path",
                    v -> copy("Artifact", run.artifact)));
            content.addView(card);
        }
    }

    private void metadata(LinearLayout content, RepositoryRecord repository) {
        RepositoryWorkspaceStore.Snapshot snapshot = RepositoryWorkspaceStore.snapshot(repository.repositoryId);
        addField(content, "Repository ID", repository.repositoryId);
        addField(content, "Configured path", repository.configuredPath);
        addField(content, "Canonical path", repository.canonicalPath);
        addField(content, "Host-visible path", repository.hostPath);
        addField(content, "Environment", repository.environment);
        addField(content, "Chroot", repository.chrootName);
        addField(content, "Linux user", repository.linuxUser);
        addField(content, "Detected project", repository.projectType);
        addField(content, "Branch", repository.branch);
        addField(content, "HEAD", snapshot.tree.headOid);
        addField(content, "Upstream", repository.upstream);
        addField(content, "Remote", repository.remote);
        addField(content, "Last scan", repository.lastScannedAt == 0 ? "Never"
                : DateFormat.getDateTimeInstance().format(new Date(repository.lastScannedAt)));
        addField(content, "Saved", String.valueOf(repository.saved));
        addField(content, "Active runs", String.valueOf(repository.activeRunCount));
        addField(content, "Indexed commits", String.valueOf(snapshot.commits.size()));
        addField(content, "Queued/running Git operations", String.valueOf(
                RepositoryWorkspaceStore.operations(this, repository.repositoryId).stream()
                        .filter(RepositoryOperationRecord::active).count()));
        addField(content, "Remote transport", snapshot.remoteDiagnostics.transport);
        content.addView(queueRecoveryCard(repository));
        content.addView(repositoryDoctorCard(repository));
        content.addView(repositoryPathResolutionCard(repository));
    }


    private View queueRecoveryCard(RepositoryRecord repository) {
        RepoQueueRecovery.QueueHealth health = RepoQueueRecovery.assess(this, repository);
        LinearLayout card = ui.surface();
        int color = health.status.equals("fail") ? ui.palette.red
                : health.status.equals("warn") ? ui.palette.yellow : ui.palette.green;
        card.addView(ui.text("Repo queue recovery", UiTypography.Role.LABEL, ui.palette.muted));
        card.addView(ui.text(health.summary, UiTypography.Role.CARD_TITLE, color));
        card.addView(ui.text(health.recommendation, UiTypography.Role.BODY_SMALL, ui.palette.text));
        card.addView(ui.text("queued=" + health.queued + " · running=" + health.running
                        + " · orphaned=" + health.orphaned + " · stale queued=" + health.staleQueued
                        + " · stale running=" + health.staleRunning,
                UiTypography.Role.CAPTION, ui.palette.muted));
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(ui.verticalActions() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        Button probe = ui.secondaryButton("Check queue health", v -> {
            RepositoryWorkspaceStore.queueHealth(this, repository);
            Toast.makeText(this, "Queue health recorded", Toast.LENGTH_SHORT).show();
            rebuild();
        });
        Button copy = ui.secondaryButton("Copy queue report", v -> copy("Queue report",
                RepoQueueRecovery.plainText(this, repository)));
        if (ui.verticalActions()) {
            actions.addView(probe);
            actions.addView(copy);
        } else {
            actions.addView(probe, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            actions.addView(copy, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        }
        card.addView(actions);
        card.addView(ui.text("Recovery actions are metadata/process-control only. They do not run Git reset, Git clean, or delete files.",
                UiTypography.Role.CAPTION, ui.palette.muted));
        return card;
    }

    private View repositoryDoctorCard(RepositoryRecord repository) {
        RepositoryDoctorResult result = RepositoryDoctor.latest(this, repository);
        LinearLayout card = ui.surface();
        int color = result.status.equals("pass") ? ui.palette.green
                : result.status.equals("fail") ? ui.palette.red
                : result.status.equals("warn") ? ui.palette.yellow : ui.palette.cyan;
        card.addView(ui.text("Repo Manager Doctor", UiTypography.Role.LABEL, ui.palette.muted));
        card.addView(ui.text(result.title, UiTypography.Role.CARD_TITLE, color));
        card.addView(ui.text(result.summary, UiTypography.Role.BODY_SMALL, ui.palette.text));
        card.addView(ui.text(result.recommendation, UiTypography.Role.CAPTION, ui.palette.muted));
        card.addView(ui.text(join(" · ", result.issueKey, result.environment, result.chrootName,
                        result.linuxUser, result.checkedAtMs == 0 ? "not run"
                                : DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
                                .format(new Date(result.checkedAtMs))),
                UiTypography.Role.CAPTION, ui.palette.muted));
        TextView path = ui.text(result.commandPath, UiTypography.Role.CAPTION, ui.palette.muted);
        path.setTypeface(Typeface.MONOSPACE);
        path.setTextIsSelectable(true);
        card.addView(path);
        card.addView(ui.text("checks: path=" + yesNo(result.pathExists)
                        + " · gitRoot=" + yesNo(result.gitDirExists)
                        + " · git=" + yesNo(result.gitAvailable)
                        + " · git -C=" + yesNo(result.gitCommandWorks)
                        + " · runuser=" + yesNo(result.runuserWorks)
                        + " · su=" + yesNo(result.suWorks),
                UiTypography.Role.CAPTION, ui.palette.muted));
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(ui.verticalActions() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        Button run = ui.button("Run Doctor", ui.palette.cyan, v -> runDoctor(repository));
        Button copy = ui.secondaryButton("Copy diagnosis", v -> copy("Repository diagnosis", result.plainText()));
        if (ui.verticalActions()) {
            actions.addView(run);
            actions.addView(copy);
        } else {
            actions.addView(run, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            actions.addView(copy, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        }
        card.addView(actions);
        return card;
    }


    private View repositoryPathResolutionCard(RepositoryRecord repository) {
        RepositoryPathResolution resolution = RepositoryPathResolver.analyze(repository);
        LinearLayout card = ui.surface();
        int color = resolution.status.equals("pass") ? ui.palette.green
                : resolution.status.equals("fail") ? ui.palette.red : ui.palette.yellow;
        card.addView(ui.text("Repository path resolver", UiTypography.Role.LABEL, ui.palette.muted));
        card.addView(ui.text(resolution.title, UiTypography.Role.CARD_TITLE, color));
        card.addView(ui.text(resolution.summary, UiTypography.Role.BODY_SMALL, ui.palette.text));
        card.addView(ui.text(resolution.recommendation, UiTypography.Role.CAPTION, ui.palette.muted));
        card.addView(ui.text("issue=" + resolution.issueKey + " · detected="
                        + resolution.detectedPathFamily + " · expected=" + resolution.expectedPathFamily,
                UiTypography.Role.CAPTION, ui.palette.muted));
        TextView path = ui.text(resolution.canonicalPath, UiTypography.Role.CAPTION, ui.palette.muted);
        path.setTypeface(Typeface.MONOSPACE);
        path.setTextIsSelectable(true);
        card.addView(path);
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(ui.verticalActions() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        Button copy = ui.secondaryButton("Copy resolution", v -> copy("Repository path resolution", resolution.plainText()));
        if (resolution.hasRepairActions()) {
            RepositoryPathResolution.RepairAction primary = resolution.primaryAction();
            Button repair = ui.button(primary.label, ui.palette.cyan, v -> confirmPathRepair(repository, primary));
            if (ui.verticalActions()) {
                actions.addView(repair);
                actions.addView(copy);
            } else {
                actions.addView(repair, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
                actions.addView(copy, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            }
            card.addView(actions);
            for (int i = 1; i < resolution.actions.size(); i++) {
                RepositoryPathResolution.RepairAction action = resolution.actions.get(i);
                card.addView(ui.secondaryButton(action.label, v -> confirmPathRepair(repository, action)));
            }
        } else {
            actions.addView(copy);
            card.addView(actions);
        }
        return card;
    }

    private void runDoctor(RepositoryRecord repository) {
        RepositoryDoctor.runAsync(this, repository);
        Toast.makeText(this, "Repository doctor started", Toast.LENGTH_SHORT).show();
    }



    private void confirmTerminateOrphan(RepositoryRecord repository, RepositoryOperationRecord operation) {
        new AlertDialog.Builder(this)
                .setTitle("Terminate orphaned operation?")
                .setMessage("This sends TERM/KILL only to a detached process tagged with this Git operation id. It does not run Git, reset files, or delete the worktree.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Terminate orphan", (dialog, which) -> {
                    boolean ok = RepositoryWorkspaceStore.terminateOrphan(this, repository.repositoryId, operation.operationId);
                    Toast.makeText(this, ok ? "Orphan recovery requested" : "Unable to terminate orphan", Toast.LENGTH_SHORT).show();
                    rebuild();
                })
                .show();
    }

    private void confirmMarkInterrupted(RepositoryRecord repository, RepositoryOperationRecord operation) {
        new AlertDialog.Builder(this)
                .setTitle("Mark operation interrupted?")
                .setMessage("This only updates Android operation metadata after review. It does not kill a process, run Git, reset files, or delete anything.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Mark interrupted", (dialog, which) -> {
                    boolean ok = RepositoryWorkspaceStore.markInterrupted(this, repository.repositoryId, operation.operationId);
                    Toast.makeText(this, ok ? "Operation marked interrupted" : "Unable to mark interrupted", Toast.LENGTH_SHORT).show();
                    rebuild();
                })
                .show();
    }

    private void confirmRetryOperation(RepositoryRecord repository, RepositoryOperationRecord operation,
                                       boolean afterPathRepair) {
        new AlertDialog.Builder(this)
                .setTitle(afterPathRepair ? "Retry after path repair?" : "Retry operation?")
                .setMessage("This queues a new Git operation using the current repository metadata and executor planner. Review the trace first for destructive operations such as hard reset.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton(afterPathRepair ? "Retry after repair" : "Retry", (dialog, which) -> {
                    boolean ok = RepositoryWorkspaceStore.retry(this, repository.repositoryId, operation.operationId, afterPathRepair);
                    Toast.makeText(this, ok ? "Retry queued" : "Retry unavailable", Toast.LENGTH_SHORT).show();
                    rebuild();
                })
                .show();
    }

    private void confirmPathRepair(RepositoryRecord repository, RepositoryPathResolution.RepairAction action) {
        new AlertDialog.Builder(this)
                .setTitle(action.label + "?")
                .setMessage(action.description + "\n\nThis changes only Android repository metadata. It does not delete files, modify Git history, reset the worktree or run Git.\n\nNew environment: "
                        + action.environment + (action.chrootName.isEmpty() ? "" : " / " + action.chrootName)
                        + "\nLinux user: " + action.linuxUser
                        + "\nPath: " + action.path)
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Apply repair", (dialog, which) -> applyPathRepair(repository, action))
                .show();
    }

    private void applyPathRepair(RepositoryRecord repository, RepositoryPathResolution.RepairAction action) {
        RepositoryRecord repaired = RepositoryStore.applyPathRepair(this, repository.repositoryId, action);
        if (repaired == null) {
            Toast.makeText(this, "Repository repair could not be applied", Toast.LENGTH_LONG).show();
            return;
        }
        repositoryId = repaired.repositoryId;
        Toast.makeText(this, "Repository metadata repaired", Toast.LENGTH_SHORT).show();
        rebuild();
        refreshWorkspace(true);
    }

    private static String yesNo(boolean value) { return value ? "yes" : "no"; }

    private void scheduleQueueTicker() {
        queueTickerHandler.removeCallbacks(queueTicker);
        if (registered && tab == 4 && RepositoryWorkspaceStore.busy(repositoryId)) {
            queueTickerHandler.postDelayed(queueTicker, 1000L);
        }
    }

    private void refreshWorkspace(boolean force) {
        long now = System.currentTimeMillis();
        if (!force && now - lastWorkspaceRefresh < 1500L) return;
        RepositoryRecord repository = RepositoryStore.find(this, repositoryId);
        if (repository == null) return;
        lastWorkspaceRefresh = now;
        RepositoryWorkspaceStore.refreshAsync(this, repository);
    }

    private void openDiff(RepositoryRecord repository, GitWorkingTree.GitChange change, boolean staged) {
        startActivity(new Intent(this, GitDiffActivity.class)
                .putExtra(GitDiffActivity.EXTRA_REPOSITORY_ID, repository.repositoryId)
                .putExtra(GitDiffActivity.EXTRA_PATH, change.path)
                .putExtra(GitDiffActivity.EXTRA_STAGED, staged)
                .putExtra(GitDiffActivity.EXTRA_UNTRACKED, change.untracked() && !staged));
    }

    private void openCommit(RepositoryRecord repository, String hash) {
        startActivity(new Intent(this, GitCommitActivity.class)
                .putExtra(GitCommitActivity.EXTRA_REPOSITORY_ID, repository.repositoryId)
                .putExtra(GitCommitActivity.EXTRA_HASH, hash));
    }

    private void confirmDiscard(RepositoryRecord repository, GitWorkingTree.GitChange change) {
        new AlertDialog.Builder(this)
                .setTitle("Discard working-tree changes?")
                .setMessage(change.path + "\n\nThe file will be restored to the index version. "
                        + "Any staged version is preserved. This cannot be undone.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Discard", (dialog, which) ->
                        RepositoryWorkspaceStore.discardWorktree(this, repository, change.path))
                .show();
    }

    private void showCommitDialog(RepositoryRecord repository) {
        EditText message = input("Commit message", true);
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Commit staged changes")
                .setMessage("Git hooks and the repository's signing configuration remain active.")
                .setView(message)
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Commit", null)
                .create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
            String value = message.getText().toString().trim();
            if (value.isEmpty()) {
                message.setError("Commit message is required");
                return;
            }
            RepositoryWorkspaceStore.commit(this, repository, value);
            dialog.dismiss();
        }));
        dialog.show();
    }

    private void showCreateBranchDialog(RepositoryRecord repository) {
        EditText branch = input("feature/name", false);
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Create and checkout branch")
                .setMessage("The name is validated with git check-ref-format before creation.")
                .setView(branch)
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Create", null)
                .create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
            String value = branch.getText().toString().trim();
            if (value.isEmpty()) {
                branch.setError("Branch name is required");
                return;
            }
            RepositoryWorkspaceStore.createBranch(this, repository, value);
            dialog.dismiss();
        }));
        dialog.show();
    }

    private void showStashDialog(RepositoryRecord repository) {
        EditText message = input("Optional stash message", false);
        new AlertDialog.Builder(this)
                .setTitle("Stash changes?")
                .setMessage("Tracked, staged, and untracked files will be stashed. Ignored files are not included.")
                .setView(message)
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Stash", (dialog, which) ->
                        RepositoryWorkspaceStore.stash(this, repository, message.getText().toString()))
                .show();
    }

    private void confirmCheckout(RepositoryRecord repository, GitBranchRecord branch) {
        RepositoryWorkspaceStore.Snapshot snapshot = RepositoryWorkspaceStore.snapshot(repository.repositoryId);
        String warning = snapshot.tree.dirty()
                ? "\n\nThe working tree is modified. Git may reject the switch or carry compatible changes to the target branch."
                : "";
        new AlertDialog.Builder(this)
                .setTitle("Checkout " + branch.name + "?")
                .setMessage((branch.remote
                        ? "A local tracking branch will be created when needed."
                        : "The working tree will switch to this local branch.") + warning)
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Checkout", (dialog, which) ->
                        RepositoryWorkspaceStore.checkout(this, repository, branch.name, branch.remote))
                .show();
    }

    private void confirmPull(RepositoryRecord repository) {
        RepositoryWorkspaceStore.Snapshot snapshot = RepositoryWorkspaceStore.snapshot(repository.repositoryId);
        String warning = snapshot.tree.dirty()
                ? "\n\nThe working tree is modified. Git may reject the pull. Consider stashing first."
                : "";
        new AlertDialog.Builder(this)
                .setTitle("Pull from " + repository.upstream + "?")
                .setMessage("Only a fast-forward update is allowed. Merge commits and rebases will not be created." + warning)
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Pull", (dialog, which) ->
                        RepositoryWorkspaceStore.pullFastForward(this, repository))
                .show();
    }

    private void confirmPush(RepositoryRecord repository) {
        String destination = repository.upstream.isEmpty()
                ? "origin or the first configured remote, with upstream tracking enabled"
                : repository.upstream;
        new AlertDialog.Builder(this)
                .setTitle("Push " + repository.branch + "?")
                .setMessage("Destination: " + destination + "\n\n"
                        + Math.max(0, repository.aheadCount) + " outgoing commit(s). Force push is never used.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Push", (dialog, which) ->
                        RepositoryWorkspaceStore.push(this, repository))
                .show();
    }

    private void confirmPopStash(RepositoryRecord repository, GitStashRecord stash) {
        new AlertDialog.Builder(this)
                .setTitle("Pop " + stash.reference + "?")
                .setMessage("The stash will be applied and dropped only when Git can complete the pop. Conflicts may require manual resolution.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Pop stash", (dialog, which) ->
                        RepositoryWorkspaceStore.popStash(this, repository, stash.reference))
                .show();
    }

    private void confirmHardReset(RepositoryRecord repository) {
        EditText confirmation = input("Type DISCARD", false);
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Hard reset tracked changes?")
                .setMessage("git reset --hard HEAD\n\nAll staged and unstaged tracked changes will be permanently discarded. "
                        + "Untracked files will not be deleted. Type DISCARD to continue.")
                .setView(confirmation)
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Hard reset", null)
                .create();
        dialog.setOnShowListener(ignored -> {
            Button positive = dialog.getButton(AlertDialog.BUTTON_POSITIVE);
            positive.setEnabled(false);
            confirmation.addTextChangedListener(new TextWatcher() {
                @Override public void beforeTextChanged(CharSequence value, int start, int count, int after) {}
                @Override public void onTextChanged(CharSequence value, int start, int before, int count) {
                    positive.setEnabled("DISCARD".contentEquals(value));
                }
                @Override public void afterTextChanged(Editable value) {}
            });
            positive.setOnClickListener(view -> {
                if (!"DISCARD".contentEquals(confirmation.getText())) return;
                RepositoryWorkspaceStore.hardReset(this, repository);
                dialog.dismiss();
            });
        });
        dialog.show();
    }

    private EditText input(String hint, boolean multiline) {
        EditText field = new EditText(this);
        field.setHint(hint);
        field.setTextColor(ui.palette.text);
        field.setHintTextColor(ui.palette.muted);
        field.setSingleLine(!multiline);
        field.setInputType(multiline
                ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
                : InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        if (multiline) {
            field.setMinLines(3);
            field.setMaxLines(8);
        }
        int padding = ui.dp(16);
        field.setPadding(padding, ui.dp(10), padding, ui.dp(10));
        return field;
    }

    private static boolean hasTrackedChanges(GitWorkingTree tree) {
        for (GitWorkingTree.GitChange change : tree.changes) if (!change.untracked()) return true;
        return false;
    }

    private void useInRun(RepositoryRecord repository) {
        DevtoolRunConfig config = AppSettings.runConfig(this);
        config.repo = repository.canonicalPath;
        config.workdir = repository.environment.equals("chroot") || repository.environment.equals("root")
                ? "~/" : repository.hostPath;
        config.projectType = repository.projectType.isEmpty() ? "auto" : repository.projectType;
        config.executionEnvironment = repository.environment.equals("chroot") ? "chroot"
                : repository.environment.equals("root") ? "android-device" : "native-termux";
        config.useChroot = repository.environment.equals("chroot");
        config.useRootBridge = repository.environment.equals("root") || config.useRootBridge;
        config.chrootName = repository.chrootName.isEmpty() ? "androidos" : repository.chrootName;
        AppSettings.setRunConfig(this, config);
        AppSettings.setRunDraft(this, config);
        RepositoryStore.touch(this, repository.repositoryId);
        Intent intent = new Intent(this, MainActivity.class)
                .putExtra("open_page", 1)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        startActivity(intent);
        finish();
    }

    private void toggleSaved(RepositoryRecord repository) {
        if (!repository.saved) {
            RepositoryStore.setSaved(this, repository.repositoryId, true);
            Toast.makeText(this, "Repository saved", Toast.LENGTH_SHORT).show();
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle("Remove saved repository?")
                .setMessage("This removes it from the Android repository list. Files, Git history, logs and runs are not deleted.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Remove", (dialog, which) -> {
                    RepositoryStore.remove(this, repository.repositoryId);
                    finish();
                }).show();
    }

    private void openShell(RepositoryRecord repository) {
        RepositoryLocation location = repository.location();
        String command;
        String workdir;
        if (location.environment.equals("chroot")) {
            String userCommand = "cd " + RootCommandBroker.shellQuote(location.path) + " && exec zsh -l";
            String inside;
            if (location.linuxUser.isEmpty() || location.linuxUser.equals("root")) {
                inside = userCommand;
            } else {
                inside = "if command -v runuser >/dev/null 2>&1; then runuser -u "
                        + RootCommandBroker.shellQuote(location.linuxUser) + " -- zsh -lc "
                        + RootCommandBroker.shellQuote(userCommand)
                        + "; else su - " + RootCommandBroker.shellQuote(location.linuxUser)
                        + " -s /bin/zsh -c " + RootCommandBroker.shellQuote(userCommand) + "; fi";
            }
            command = "sudo chrootctl --name " + RootCommandBroker.shellQuote(location.chrootName)
                    + " exec -- zsh -lc " + RootCommandBroker.shellQuote(inside);
            workdir = "~/";
        } else if (location.environment.equals("root")) {
            String rootShell = "cd " + RootCommandBroker.shellQuote(location.path)
                    + " && exec /system/bin/sh";
            command = "su -c " + RootCommandBroker.shellQuote(rootShell);
            workdir = "~/";
        } else {
            command = "exec zsh -l";
            workdir = location.hostPath.isEmpty() ? location.path : location.hostPath;
        }
        TermuxCommandRunner.LaunchResult result = TermuxCommandRunner.runUtilityCommand(
                this, command, workdir, false, "repository-shell");
        if (!result.started) Toast.makeText(this, result.error, Toast.LENGTH_LONG).show();
    }

    private void browse(RepositoryRecord repository) {
        RepositoryLocation location = repository.location();
        startActivity(new Intent(this, RepositoryPickerActivity.class)
                .putExtra(RepositoryPickerActivity.EXTRA_MODE, RepositoryPickerActivity.MODE_BROWSE)
                .putExtra(RepositoryPickerActivity.EXTRA_ENVIRONMENT, location.environment)
                .putExtra(RepositoryPickerActivity.EXTRA_CHROOT, location.chrootName)
                .putExtra(RepositoryPickerActivity.EXTRA_USER, location.linuxUser)
                .putExtra(RepositoryPickerActivity.EXTRA_PATH, location.path));
    }

    private void openRepositoryLogs(RepositoryRecord repository) {
        startActivity(new Intent(this, MainActivity.class)
                .putExtra("open_page", 2)
                .putExtra("logs_repository_id", repository.repositoryId)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP));
    }

    private void openLog(String runId) {
        startActivity(new Intent(this, LogViewerActivity.class).putExtra(LogViewerActivity.EXTRA_RUN_ID, runId));
    }

    private void addField(LinearLayout content, String label, String value) {
        if (value == null || value.trim().isEmpty()) return;
        LinearLayout card = ui.surface();
        card.addView(ui.text(label, UiTypography.Role.LABEL, ui.palette.muted));
        TextView text = ui.text(value, UiTypography.Role.BODY_SMALL, ui.palette.text);
        text.setTextIsSelectable(true);
        if (label.contains("path") || label.contains("ID") || label.equals("Remote") || label.equals("HEAD")) {
            text.setTypeface(Typeface.MONOSPACE);
        }
        card.addView(text);
        content.addView(card);
    }

    private void empty(LinearLayout content, String title, String detail) {
        LinearLayout card = ui.surface();
        card.addView(ui.text(title, UiTypography.Role.CARD_TITLE, ui.palette.text));
        card.addView(ui.text(detail, UiTypography.Role.BODY_SMALL, ui.palette.muted));
        content.addView(card);
    }

    private void copy(String label, String value) {
        ClipboardManager manager = getSystemService(ClipboardManager.class);
        if (manager != null) manager.setPrimaryClip(ClipData.newPlainText(label, value));
        Toast.makeText(this, label + " copied", Toast.LENGTH_SHORT).show();
    }

    private int statusColor(String status) {
        if (status.equals("clean")) return ui.palette.green;
        if (status.equals("modified")) return ui.palette.yellow;
        if (status.equals("checking")) return ui.palette.cyan;
        return ui.palette.red;
    }

    private static String aheadBehind(RepositoryRecord repository) {
        if (repository.aheadCount == 0 && repository.behindCount == 0) return "";
        return repository.aheadCount + " ahead · " + repository.behindCount + " behind";
    }

    private static Uri remoteUrl(String remote) {
        String value = remote == null ? "" : remote.trim();
        if (value.startsWith("git@")) {
            int colon = value.indexOf(':');
            if (colon > 4) value = "https://" + value.substring(4, colon) + "/" + value.substring(colon + 1);
        }
        if (value.startsWith("ssh://git@")) value = "https://" + value.substring(10);
        if (value.endsWith(".git")) value = value.substring(0, value.length() - 4);
        if (!value.startsWith("https://") && !value.startsWith("http://")) return null;
        return Uri.parse(value);
    }

    private static String duration(long value) {
        long seconds = Math.max(0L, value / 1000L);
        if (seconds < 60) return seconds + "s";
        return seconds / 60 + "m " + seconds % 60 + "s";
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
