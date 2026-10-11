package io.github.mikeceead.devtoolhud;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.NotificationManager;
import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.Editable;
import android.text.TextUtils;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowInsets;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import java.io.InputStream;
import java.io.OutputStream;
import java.text.DateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int REQUEST_NOTIFICATIONS = 41;
    private static final int REQUEST_TERMUX = 42;
    private static final int REQUEST_ROOT_MODULE_EXPORT = 43;
    private static final int HISTORY_PAGE_SIZE = 100;
    private static final long LOG_FILTER_DEBOUNCE_MS = 250L;
    private static final String[] PAGES = {"Overview", "Run", "Logs", "Repos", "Settings"};

    private UiKit ui;
    private int currentPage;
    private boolean runValidationSection;
    private String requestedValidationSessionId = "";
    private View validationActionBar;
    private TextView statusView;
    private TextView tokenView;
    private TextView commandPreview;
    private Spinner operationInput;
    private Spinner projectTypeInput;
    private Spinner executionModeInput;
    private EditText repoInput;
    private EditText runWorkdirInput;
    private EditText targetInput;
    private EditText artifactInput;
    private EditText commitInput;
    private EditText customInput;
    private EditText environmentInput;
    private EditText extraGlobalInput;
    private EditText extraOperationInput;
    private EditText chrootInput;
    private EditText testProfileInput;
    private EditText testSelectorsInput;
    private EditText testTagsInput;
    private EditText testExcludeTagsInput;
    private EditText testPathInput;
    private EditText testClassInput;
    private EditText testMethodInput;
    private Switch chrootToggle;
    private Switch rootBridgeToggle;
    private Switch copyToggle;
    private Switch copyViaTermuxToggle;
    private Switch autoHudToggle;
    private Switch validateToggle;
    private Switch commitToggle;
    private Switch yesToggle;
    private Switch runOverlayToggle;
    private Switch listTestsToggle;
    private Switch instrumentedToggle;
    private EditText rawCommandInput;
    private EditText workdirInput;
    private Switch foregroundToggle;
    private Switch overlayDefaultToggle;
    private boolean receiverRegistered;
    private DevtoolRunConfig runDraft;
    private boolean syncingExecutionControls;
    private int runStage;
    private LinearLayout runProjectSection;
    private LinearLayout runOperationSection;
    private LinearLayout runTestsSection;
    private LinearLayout runReviewSection;
    private LinearLayout runProjectAdvancedSection;
    private LinearLayout runOperationAdvancedSection;
    private LinearLayout runReviewAdvancedSection;
    private LinearLayout runDangerZoneSection;
    private boolean runProjectAdvancedOpen;
    private boolean runOperationAdvancedOpen;
    private boolean runReviewAdvancedOpen;
    private boolean runDangerZoneOpen;
    private Button runBackButton;
    private Button runNextButton;
    private Button runExecuteButton;
    private TextView runStageLabel;
    private EditText testCatalogSearch;
    private LinearLayout testCatalogList;
    private List<TestCatalog.Item> testCatalogItems = new ArrayList<>();
    private final Set<String> selectedTestIds = new LinkedHashSet<>();
    private String historyQuery = "";
    private String historyStatus = "all";
    private String historyProject = "all";
    private String historySeverity = "all";
    private String historyRepositoryId = "";
    private String repositoryQuery = "";
    private String repositoryFilter = "all";
    private String repositoryError = "";
    private String logIndexError = "";
    private int historyVisibleLimit = HISTORY_PAGE_SIZE;
    private int historyLoadGeneration;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService historyExecutor = Executors.newSingleThreadExecutor();
    private Runnable pendingHistoryRefresh;

    private final BroadcastReceiver runUpdates = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (RepositoryStore.ACTION_UPDATED.equals(intent.getAction())) {
                String error = intent.getStringExtra(RepositoryStore.EXTRA_ERROR);
                if (error != null) repositoryError = error;
            } else if (RootLogIndexer.ACTION_UPDATED.equals(intent.getAction())) {
                String error = intent.getStringExtra(RootLogIndexer.EXTRA_ERROR);
                if (error != null) logIndexError = error;
            } else if (ValidationStore.ACTION_UPDATED.equals(intent.getAction())) {
                String error = intent.getStringExtra(ValidationStore.EXTRA_ERROR);
                if (error != null && !error.trim().isEmpty()) Toast.makeText(MainActivity.this, error, Toast.LENGTH_LONG).show();
            }
            if (currentPage != 1 || runValidationSection) rebuildUi();
        }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        new NotificationController(this);
        RepositoryWorkspaceStore.initialize(this);
        RunStore.reconcileStale(this); // Complete migration and stale-run cleanup before retention evaluates rows.
        RetentionManager.maybeRun(this);
        historyQuery = AppSettings.logQuery(this);
        historyStatus = AppSettings.logStatus(this);
        historyProject = AppSettings.logProject(this);
        historySeverity = AppSettings.logSeverity(this);
        historyRepositoryId = AppSettings.logRepository(this);
        applyLogRepositoryIntent(getIntent());
        applyValidationIntent(getIntent());
        currentPage = resolveRequestedPage(getIntent());
        rebuildUi();
        maybeOpenRequestedRun(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (intent != null) {
            applyLogRepositoryIntent(intent);
            applyValidationIntent(intent);
            currentPage = resolveRequestedPage(intent);
            AppSettings.setPage(this, currentPage);
            rebuildUi();
            maybeOpenRequestedRun(intent);
        }
    }

    @Override
    @SuppressLint("UnspecifiedRegisterReceiverFlag")
    protected void onStart() {
        super.onStart();
        if (!receiverRegistered) {
            IntentFilter filter = new IntentFilter(RunStore.ACTION_UPDATED);
            filter.addAction(BridgeTokenManager.ACTION_UPDATED);
        filter.addAction(BridgeRoundtripProbe.ACTION_UPDATED);
            filter.addAction(RootBridgeManager.ACTION_UPDATED);
            filter.addAction(RepositoryStore.ACTION_UPDATED);
            filter.addAction(RootLogIndexer.ACTION_UPDATED);
            filter.addAction(ValidationStore.ACTION_UPDATED);
            if (Build.VERSION.SDK_INT >= 33) registerReceiver(runUpdates, filter, Context.RECEIVER_NOT_EXPORTED);
            else registerReceiver(runUpdates, filter);
            receiverRegistered = true;
        }
    }

    @Override
    protected void onStop() {
        captureRunDraft();
        if (pendingHistoryRefresh != null) mainHandler.removeCallbacks(pendingHistoryRefresh);
        if (receiverRegistered) {
            unregisterReceiver(runUpdates);
            receiverRegistered = false;
        }
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        historyLoadGeneration++;
        historyExecutor.shutdownNow();
        super.onDestroy();
    }

    @Override
    protected void onResume() {
        super.onResume();
        RunStore.reconcileStale(this);
        if (ui != null && (currentPage == 0 || currentPage == 3 || currentPage == 4 || (currentPage == 1 && runValidationSection))) rebuildUi();
        else if (statusView != null) refreshStatus();
    }

    private void rebuildUi() {
        ui = new UiKit(this);
        getWindow().setStatusBarColor(ui.palette.background);
        getWindow().setNavigationBarColor(ui.palette.background);
        setContentView(buildRoot());
        refreshStatus();
    }

    private View buildRoot() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(ui.wideLayout() ? LinearLayout.HORIZONTAL : LinearLayout.VERTICAL);
        root.setBackgroundColor(ui.palette.background);
        root.setFocusableInTouchMode(true);
        root.setContentDescription(PAGES[currentPage] + ". " + pageSubtitle());
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            int top;
            int bottom;
            int left = 0;
            int right = 0;
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                top = bars.top;
                bottom = bars.bottom;
                left = bars.left;
                right = bars.right;
            } else {
                top = insets.getSystemWindowInsetTop();
                bottom = insets.getSystemWindowInsetBottom();
            }
            view.setPadding(left, top, right, bottom);
            return insets;
        });

        LinearLayout pageColumn = new LinearLayout(this);
        pageColumn.setOrientation(LinearLayout.VERTICAL);
        pageColumn.setBackgroundColor(ui.palette.background);
        pageColumn.addView(buildPageHeader());

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(ui.palette.background);
        scroll.addView(buildPage());
        pageColumn.addView(scroll, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        if (currentPage == 1) {
            if (runValidationSection && validationActionBar != null) pageColumn.addView(validationActionBar);
            else pageColumn.addView(buildRunActionBar());
        }

        if (ui.wideLayout()) {
            root.addView(buildNavigationRail(), new LinearLayout.LayoutParams(
                    ui.dp(AdaptiveLayout.navigationRailWidthDp(this)),
                    LinearLayout.LayoutParams.MATCH_PARENT));
            root.addView(pageColumn, new LinearLayout.LayoutParams(
                    0, LinearLayout.LayoutParams.MATCH_PARENT, 1f));
        } else {
            root.addView(pageColumn, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
            root.addView(buildBottomNavigation());
        }
        return root;
    }

    private View buildPageHeader() {
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(ui.contentHorizontalPadding(), ui.dp(ui.wideLayout() ? 18 : 12),
                ui.contentHorizontalPadding(), ui.dp(ui.wideLayout() ? 14 : 9));
        if (ui.wideLayout()) {
            header.addView(ui.text("DEVTOOL", UiTypography.Role.LABEL, ui.palette.cyan));
        }
        TextView title = ui.text(PAGES[currentPage], UiTypography.Role.PAGE_TITLE, ui.palette.text);
        title.setPadding(0, ui.dp(ui.wideLayout() ? 2 : 0), 0, ui.dp(ui.wideLayout() ? 2 : 1));
        if (Build.VERSION.SDK_INT >= 28) title.setAccessibilityHeading(true);
        title.setContentDescription(PAGES[currentPage] + " page");
        header.addView(title);
        TextView subtitle = ui.text(pageSubtitle(), ui.wideLayout() ? UiTypography.Role.BODY : UiTypography.Role.BODY_SMALL, ui.palette.muted);
        subtitle.setMaxLines(ui.extraLargeText() ? 2 : 1);
        subtitle.setEllipsize(TextUtils.TruncateAt.END);
        header.addView(subtitle);
        return header;
    }

    private View buildNavigationRail() {
        LinearLayout rail = new LinearLayout(this);
        rail.setOrientation(LinearLayout.VERTICAL);
        rail.setPadding(ui.dp(10), ui.dp(14), ui.dp(10), ui.dp(14));
        rail.setBackgroundColor(ui.palette.surface);
        TextView brand = ui.text("DEVTOOL", UiTypography.Role.LABEL, ui.palette.cyan);
        brand.setPadding(ui.dp(10), ui.dp(4), ui.dp(10), ui.dp(14));
        rail.addView(brand);
        int[] symbols = {R.drawable.ic_nav_overview, R.drawable.ic_nav_run,
                R.drawable.ic_nav_history, R.drawable.ic_repository, R.drawable.ic_nav_settings};
        for (int index = 0; index < PAGES.length; index++) {
            final int page = index;
            Button button = ui.navButton(symbols[index], PAGES[index], index == currentPage, v -> {
                currentPage = page;
                AppSettings.setPage(this, page);
                rebuildUi();
            });
            button.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
            button.setCompoundDrawablesWithIntrinsicBounds(symbols[index], 0, 0, 0);
            button.setCompoundDrawablePadding(ui.dp(12));
            button.setPadding(ui.dp(12), ui.dp(10), ui.dp(10), ui.dp(10));
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
            params.setMargins(0, ui.dp(3), 0, ui.dp(3));
            rail.addView(button, params);
        }
        return rail;
    }

    private View buildBottomNavigation() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(ui.dp(10), ui.dp(7), ui.dp(10), ui.dp(7));
        row.setBackgroundColor(ui.palette.surface);
        int[] symbols = {R.drawable.ic_nav_overview, R.drawable.ic_nav_run, R.drawable.ic_nav_history, R.drawable.ic_repository, R.drawable.ic_nav_settings};
        for (int index = 0; index < PAGES.length; index++) {
            final int page = index;
            Button button = ui.navButton(symbols[index], PAGES[index], index == currentPage, v -> {
                currentPage = page;
                AppSettings.setPage(this, page);
                rebuildUi();
            });
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
            params.setMargins(ui.dp(2), 0, ui.dp(2), 0);
            button.setLayoutParams(params);
            row.addView(button);
        }
        return row;
    }

    private String pageSubtitle() {
        if (currentPage == 1) return runValidationSection
                ? "Control durable validation phases, retries and recovery."
                : "Build and run a devtool workflow without writing shell commands.";
        if (currentPage == 2) return "Search run output, problems and metadata.";
        if (currentPage == 3) return "Inspect saved, discovered and live repositories across Termux and chroots.";
        if (currentPage == 4) return "Manage appearance, permissions, pairing and advanced execution.";
        return "See readiness, current work and the next useful action at a glance.";
    }

    private View buildPage() {
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(ui.contentHorizontalPadding(), ui.dp(2),
                ui.contentHorizontalPadding(), ui.dp(28));
        if (currentPage == 1) buildRunPage(content);
        else if (currentPage == 2) buildHistoryPage(content);
        else if (currentPage == 3) buildRepositoriesPage(content);
        else if (currentPage == 4) buildSettingsPage(content);
        else buildOverviewPage(content);
        return content;
    }

    private void buildOverviewPage(LinearLayout content) {
        LinearLayout primary = ui.surface();
        primary.addView(ui.sectionTitle("Next action"));
        int running = 0;
        for (RunRecord record : RunStore.all(this)) if ("running".equals(record.status)) running++;
        final boolean hasRunning = running > 0;
        String primaryTitle = hasRunning ? "Open live work" : "Run Devtool";
        primary.addView(ui.text(primaryTitle, UiTypography.Role.LARGE_VALUE, ui.palette.text));
        primary.addView(ui.text(hasRunning
                ? running + " active run" + (running == 1 ? " is" : "s are") + " publishing progress."
                : "Build a workflow, validate the current repo, or apply an overlay without writing shell commands.",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        primary.addView(ui.button(hasRunning ? "Open HUD" : "Create a run", ui.palette.cyan,
                v -> { if (hasRunning) OverlayHudService.setEnabled(this, true); else switchPage(1); }));
        if (readinessCount() == 4) primary.addView(ui.secondaryButton("Validate current repo", v -> runQuick("validate")));
        content.addView(primary);

        content.addView(ui.sectionTitle("Latest run"));
        RunRecord latest = RunStore.latest(this);
        if (latest == null) {
            LinearLayout empty = ui.surface();
            empty.addView(ui.text("No devtool runs yet", UiTypography.Role.LARGE_VALUE, ui.palette.text));
            empty.addView(ui.text("Create a workflow first; logs and artifacts will appear here after the first run.", UiTypography.Role.BODY, ui.palette.muted));
            empty.addView(ui.button("Create a run", ui.palette.cyan, v -> switchPage(1)));
            content.addView(empty);
        } else {
            content.addView(runCard(latest, true));
        }

        content.addView(ui.sectionTitle("Readiness"));
        LinearLayout readiness = ui.surface();
        int ready = readinessCount();
        readiness.addView(ui.text(ready == 4 ? "Ready to run" : ready + " of 4 services ready", UiTypography.Role.LARGE_VALUE, ready == 4 ? ui.palette.green : ui.palette.text));
        TextView readinessDetail = ui.text(ready == 4 ? "Android, Termux and the secure HUD bridge are connected."
                        : "Complete the highlighted tiles below before relying on live background progress.", UiTypography.Role.BODY_SMALL, ui.palette.muted);
        readinessDetail.setPadding(0, ui.dp(5), 0, 0);
        readiness.addView(readinessDetail);
        content.addView(readiness);

        content.addView(ui.sectionTitle("Quick actions"));
        content.addView(ui.quickTile(R.drawable.ic_validate, "Validate target", "Restore, build and test efficiently", ui.palette.cyan, v -> runQuick("validate")));
        content.addView(ui.quickTile(R.drawable.ic_workflow, "Build workflow", "Apply overlays, test, package or commit", ui.palette.yellow, v -> switchPage(1)));
        content.addView(ui.quickTile(R.drawable.ic_nav_history, "Logs", "Review output, problems and metadata", ui.palette.muted, v -> switchPage(2)));
        content.addView(ui.quickTile(R.drawable.ic_repository, "Repos", "Inspect Termux and chroot repositories", ui.palette.green, v -> switchPage(3)));
        content.addView(ui.quickTile(R.drawable.ic_tracker, "Debug Workbench", "Open run trace, bridge and repo diagnostics", ui.palette.yellow, v -> openDebugWorkbench()));

        addCoreTiles(content, false);
        content.addView(ui.sectionTitle("Advanced setup"));
        content.addView(rootBridgeTile());

        List<RunRecord> records = RunStore.all(this);
        if (records.size() > 1) {
            content.addView(ui.sectionTitle("Recent"));
            for (int i = 1; i < Math.min(records.size(), 4); i++) content.addView(runCard(records.get(i), false));
        }
    }

    private void buildRunPage(LinearLayout content) {
        LinearLayout mode = new LinearLayout(this);
        mode.setOrientation(LinearLayout.HORIZONTAL);
        Button commandMode = ui.secondaryButton("New command", v -> {
            runValidationSection = false; AppSettings.setRunSection(this, "command"); rebuildUi();
        });
        Button validationMode = ui.secondaryButton("Validation", v -> {
            captureRunDraft(); runValidationSection = true; AppSettings.setRunSection(this, "validation"); rebuildUi();
        });
        commandMode.setTextColor(runValidationSection ? ui.palette.muted : ui.palette.green);
        validationMode.setTextColor(runValidationSection ? ui.palette.green : ui.palette.muted);
        LinearLayout.LayoutParams modeParams = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        modeParams.setMargins(ui.dp(3), 0, ui.dp(3), ui.dp(8));
        mode.addView(commandMode, modeParams);
        LinearLayout.LayoutParams validationModeParams = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        validationModeParams.setMargins(ui.dp(3), 0, ui.dp(3), ui.dp(8));
        mode.addView(validationMode, validationModeParams);
        content.addView(mode);
        if (runValidationSection) {
            ValidationSessionRecord validationSession = requestedValidationSessionId.isEmpty()
                    ? ValidationStore.active(this) : ValidationStore.find(this, requestedValidationSessionId);
            if (validationSession == null) validationSession = ValidationStore.latest(this);
            String fallbackRepo = AppSettings.runConfig(this).repo;
            ValidationWorkbenchView workbench = new ValidationWorkbenchView(this, ui, validationSession, this::rebuildUi);
            content.addView(workbench.buildPage(fallbackRepo));
            validationActionBar = workbench.buildActionBar();
            return;
        }
        validationActionBar = null;
        DevtoolRunConfig saved = runDraft != null ? runDraft.copy() : AppSettings.runDraft(this);
        if (saved == null) saved = AppSettings.runConfig(this);
        runDraft = saved.copy();
        selectedTestIds.clear();
        for (String id : saved.testSelectors.split("[,\\n]")) if (!id.trim().isEmpty()) selectedTestIds.add(id.trim());
        testCatalogItems = TestCatalog.fromRuns(RunStore.all(this));

        runProjectSection = ui.surface();
        runProjectSection.addView(ui.sectionTitle("1 · Project"));
        runProjectSection.addView(ui.button("Paste and import command", ui.palette.cyan, v -> importBuilderCommandFromClipboard()));
        runProjectSection.addView(ui.text("Basic", UiTypography.Role.LABEL, ui.palette.muted));
        repoInput = field(runProjectSection, "Repository", saved.repo, "Termux or chroot repository path", false);
        targetInput = field(runProjectSection, "Target", saved.target, "Configured devtool target", false);
        projectTypeInput = spinner(DevtoolCommandBuilder.PROJECT_TYPES, saved.projectType, "Project type");
        runProjectAdvancedSection = progressiveSection(runProjectSection, "Project options", "Show project options", "Hide project options", () -> runProjectAdvancedOpen = !runProjectAdvancedOpen, runProjectAdvancedOpen);
        runProjectAdvancedSection.addView(label("Project type")); runProjectAdvancedSection.addView(projectTypeInput);
        runProjectAdvancedSection.addView(ui.text("Auto is recommended unless a repository needs a forced runner.", UiTypography.Role.BODY_SMALL, ui.palette.muted));
        content.addView(runProjectSection);

        runOperationSection = ui.surface();
        runOperationSection.addView(ui.sectionTitle("2 · Operation"));
        runOperationSection.addView(ui.text("Basic", UiTypography.Role.LABEL, ui.palette.muted));
        operationInput = spinner(DevtoolCommandBuilder.OPERATIONS, saved.operation, "Operation");
        runOperationSection.addView(label("Operation")); runOperationSection.addView(operationInput);
        artifactInput = field(runOperationSection, "Overlay / bundle", saved.artifact, "$HOME/Downloads/example_overlay.zip", false);
        validateToggle = toggle(runOperationSection, "Validate after apply", saved.validate);
        commitToggle = toggle(runOperationSection, "Commit after apply", saved.commit);
        commitInput = field(runOperationSection, "Commit message", saved.commitMessage, "Required for explicit commit", false);
        executionModeInput = spinner(DevtoolCommandBuilder.EXECUTION_ENVIRONMENTS, saved.executionEnvironment, "Execution environment");
        rootBridgeToggle = ui.toggle("Use optional root bridge", saved.useRootBridge);
        rootBridgeToggle.setEnabled(RootBridgeManager.isReady(this)); if (!RootBridgeManager.isReady(this)) rootBridgeToggle.setChecked(false);
        chrootToggle = ui.toggle("Run through androidos chroot", saved.useChroot);
        chrootInput = ui.input(saved.chrootName, "androidos", false);
        runWorkdirInput = ui.input(saved.workdir, "Auto: repository, or ~/ for chroot", false);
        customInput = ui.input(saved.customArguments, "Used only when operation is custom", true);
        runOperationAdvancedSection = progressiveSection(runOperationSection, "Advanced execution", "Show advanced execution", "Hide advanced execution", () -> runOperationAdvancedOpen = !runOperationAdvancedOpen, runOperationAdvancedOpen);
        runOperationAdvancedSection.addView(label("Execution environment")); runOperationAdvancedSection.addView(executionModeInput);
        runOperationAdvancedSection.addView(rootBridgeToggle);
        runOperationAdvancedSection.addView(chrootToggle);
        TextView chrootLabel = label("Chroot name"); ui.labelInput(chrootLabel, chrootInput, "Chroot name", "androidos"); runOperationAdvancedSection.addView(chrootLabel); runOperationAdvancedSection.addView(chrootInput);
        TextView workdirLabel = label("Termux working directory"); ui.labelInput(workdirLabel, runWorkdirInput, "Termux working directory", "Auto: repository, or ~/ for chroot"); runOperationAdvancedSection.addView(workdirLabel); runOperationAdvancedSection.addView(runWorkdirInput);
        TextView customLabel = label("Custom subcommand and arguments"); ui.labelInput(customLabel, customInput, "Custom subcommand and arguments", "Used only when operation is custom"); runOperationAdvancedSection.addView(customLabel); runOperationAdvancedSection.addView(customInput);
        content.addView(runOperationSection);

        runTestsSection = ui.surface();
        runTestsSection.addView(ui.sectionTitle("3 · Test selection"));
        runTestsSection.addView(ui.text("Discover the real devtool test catalog, search it, and select tests without typing framework IDs.", UiTypography.Role.BODY_SMALL, ui.palette.muted));
        testProfileInput = field(runTestsSection, "Saved profile", saved.testProfile, "quick, full, parser…", false);
        runTestsSection.addView(ui.button("Discover tests", ui.palette.cyan, v -> discoverTestsOnAndroid()));
        testCatalogSearch = ui.input("", "Search discovered tests", false); runTestsSection.addView(testCatalogSearch);
        testCatalogList = new LinearLayout(this); testCatalogList.setOrientation(LinearLayout.VERTICAL); runTestsSection.addView(testCatalogList);
        testSelectorsInput = field(runTestsSection, "Selectors", saved.testSelectors, "Selected IDs or manual selectors", true);
        testTagsInput = field(runTestsSection, "Include tags", saved.testTags, "fast, manifest", false);
        testExcludeTagsInput = field(runTestsSection, "Exclude tags", saved.testExcludeTags, "slow, network", false);
        testPathInput = field(runTestsSection, "Source path", saved.testPath, "tests/parser", false);
        testClassInput = field(runTestsSection, "Test class", saved.testClass, "ManifestParserTests", false);
        testMethodInput = field(runTestsSection, "Test method", saved.testMethod, "ValidManifest", false);
        listTestsToggle = toggle(runTestsSection, "List matching tests instead of running", saved.listTests);
        instrumentedToggle = toggle(runTestsSection, "Android instrumentation tests", saved.instrumented);
        content.addView(runTestsSection);

        runReviewSection = ui.surface();
        runReviewSection.addView(ui.sectionTitle("4 · Review"));
        runReviewSection.addView(ui.text("Review the workflow first. Output routing, environment flags and exact command details are tucked below.", UiTypography.Role.BODY_SMALL, ui.palette.muted));
        runReviewSection.addView(ui.button("Show exact command", ui.palette.cyan, v -> {
            commandPreview.setVisibility(commandPreview.getVisibility() == View.VISIBLE ? View.GONE : View.VISIBLE);
            ((Button) v).setText(commandPreview.getVisibility() == View.VISIBLE ? "Hide exact command" : "Show exact command");
        }));
        copyToggle = ui.toggle("Copy result", saved.copy);
        copyViaTermuxToggle = ui.toggle("Use Termux clipboard route", saved.copyViaTermux);
        autoHudToggle = ui.toggle("Live Android progress", saved.autoHud);
        foregroundToggle = ui.toggle("Open Termux in foreground while running", !AppSettings.runInBackground(this));
        runOverlayToggle = ui.toggle("Show floating HUD", saved.overlay);
        environmentInput = ui.input(saved.environmentAssignments, "NAME=value", false);
        extraGlobalInput = ui.input(saved.extraGlobalArguments, "Flags before the subcommand", true);
        extraOperationInput = ui.input(saved.extraOperationArguments, "Arguments after the subcommand", true);
        runReviewAdvancedSection = progressiveSection(runReviewSection, "Advanced output", "Show advanced output", "Hide advanced output", () -> runReviewAdvancedOpen = !runReviewAdvancedOpen, runReviewAdvancedOpen);
        runReviewAdvancedSection.addView(copyToggle);
        runReviewAdvancedSection.addView(copyViaTermuxToggle);
        runReviewAdvancedSection.addView(autoHudToggle);
        runReviewAdvancedSection.addView(foregroundToggle);
        runReviewAdvancedSection.addView(runOverlayToggle);
        TextView envLabel = label("Environment assignments"); ui.labelInput(envLabel, environmentInput, "Environment assignments", "NAME=value"); runReviewAdvancedSection.addView(envLabel); runReviewAdvancedSection.addView(environmentInput);
        TextView globalLabel = label("Additional global arguments"); ui.labelInput(globalLabel, extraGlobalInput, "Additional global arguments", "Flags before the subcommand"); runReviewAdvancedSection.addView(globalLabel); runReviewAdvancedSection.addView(extraGlobalInput);
        TextView opLabel = label("Additional operation arguments"); ui.labelInput(opLabel, extraOperationInput, "Additional operation arguments", "Arguments after the subcommand"); runReviewAdvancedSection.addView(opLabel); runReviewAdvancedSection.addView(extraOperationInput);
        yesToggle = ui.toggle("Non-interactive --yes", saved.assumeYes);
        runDangerZoneSection = progressiveSection(runReviewSection, "Danger zone", "Show danger zone", "Hide danger zone", () -> runDangerZoneOpen = !runDangerZoneOpen, runDangerZoneOpen);
        runDangerZoneSection.addView(yesToggle);
        runDangerZoneSection.addView(ui.text("Use this for intentional noninteractive writes only. Destructive operations still ask for confirmation before launch.", UiTypography.Role.BODY_SMALL, ui.palette.yellow));
        commandPreview = ui.text("", UiTypography.Role.BODY_SMALL, ui.palette.text); commandPreview.setTypeface(Typeface.MONOSPACE); commandPreview.setTextIsSelectable(true); commandPreview.setPadding(ui.dp(12),ui.dp(10),ui.dp(12),ui.dp(10)); commandPreview.setBackground(ui.flat(ui.palette.surfaceRaised,10)); commandPreview.setContentDescription("Generated devtool command preview"); commandPreview.setVisibility(View.GONE);
        runReviewSection.addView(commandPreview);
        runReviewSection.addView(ui.secondaryButton("Copy command", v -> copyText("Devtool command", commandPreview.getText().toString())));
        runReviewSection.addView(ui.secondaryButton("Save as defaults", v -> { saveRunConfig(); AppSettings.clearRunDraft(this); runDraft=readRunConfig().copy(); Toast.makeText(this,"Run defaults saved",Toast.LENGTH_SHORT).show(); }));
        content.addView(runReviewSection);

        operationInput.setOnItemSelectedListener(new SimpleItemSelectedListener(() -> { updateOperationVisibility(); updatePreview(); }));
        projectTypeInput.setOnItemSelectedListener(new SimpleItemSelectedListener(this::updatePreview));
        executionModeInput.setOnItemSelectedListener(new SimpleItemSelectedListener(() -> { syncExecutionControls(false); updatePreview(); }));
        watchPreview(repoInput, runWorkdirInput, targetInput, artifactInput, commitInput, customInput, chrootInput,
                testProfileInput, testSelectorsInput, testTagsInput, testExcludeTagsInput, testPathInput, testClassInput, testMethodInput,
                environmentInput, extraGlobalInput, extraOperationInput);
        testCatalogSearch.addTextChangedListener(new TextWatcher(){public void beforeTextChanged(CharSequence s,int a,int b,int c){} public void onTextChanged(CharSequence s,int a,int b,int c){populateTestCatalog();} public void afterTextChanged(Editable s){} });
        chrootToggle.setOnCheckedChangeListener((button,checked)->{syncExecutionControls(true);updatePreview();});
        for(Switch value:new Switch[]{rootBridgeToggle,copyToggle,copyViaTermuxToggle,autoHudToggle,foregroundToggle,validateToggle,commitToggle,yesToggle,runOverlayToggle,listTestsToggle,instrumentedToggle}) value.setOnCheckedChangeListener((button,checked)->updatePreview());
        syncExecutionControls(false); updateOperationVisibility(); updatePreview(); populateTestCatalog(); showRunStage(runStage);
    }

    private void buildHistoryPage(LinearLayout content) {
        RootLogIndexer.refreshIfStale(this, 60_000L);
        content.addView(ui.sectionTitle("Log sources"));
        LinearLayout sourceControls = ui.surface();
        sourceControls.addView(ui.text("Root indexing reads authoritative Devtool logs from Termux, chroots and HUD bridges. Files are tailed incrementally; source logs are never modified.",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        if (!logIndexError.isEmpty()) sourceControls.addView(ui.text(logIndexError, UiTypography.Role.LABEL, ui.palette.red));
        sourceControls.addView(ui.button(RootLogIndexer.busy() ? "Indexing…" : "Refresh root logs", ui.palette.cyan, v -> {
            logIndexError = "";
            RootLogIndexer.refreshAsync(this);
            rebuildUi();
        }));
        content.addView(sourceControls);
        content.addView(ui.sectionTitle("Search and filters"));
        LinearLayout filters = ui.surface();
        EditText search = ui.input(historyQuery, "Search command, repository, target, problem or output", false);
        search.setContentDescription("Search command, repository, target, output or structured log entries");
        filters.addView(search);
        Spinner status = spinner(new String[]{"all", "ok", "failed", "cancelled", "interrupted", "skipped", "waiting"},
                historyStatus, "Log status filter");
        filters.addView(label("Status"));
        filters.addView(status);
        Spinner project = spinner(new String[]{"all", "git", "android", "gradle", "dotnet", "python", "rust", "node", "cmake", "auto"},
                historyProject, "Project type filter");
        filters.addView(label("Project type"));
        filters.addView(project);
        Spinner severity = spinner(new String[]{"all", "error", "warning", "success", "info"},
                historySeverity, "Severity filter");
        filters.addView(label("Severity"));
        filters.addView(severity);

        List<RepositoryRecord> logRepositories = RepositoryStore.all(this);
        List<String> repositoryLabels = new ArrayList<>();
        List<String> repositoryIds = new ArrayList<>();
        repositoryLabels.add("all repositories");
        repositoryIds.add("");
        int repositorySelection = 0;
        for (RepositoryRecord repository : logRepositories) {
            repositoryLabels.add(repository.name() + " · " + repository.location().displayEnvironment());
            repositoryIds.add(repository.repositoryId);
            if (repository.repositoryId.equals(historyRepositoryId)) repositorySelection = repositoryIds.size() - 1;
        }
        Spinner repository = new Spinner(this);
        ArrayAdapter<String> repositoryAdapter = new ArrayAdapter<>(this,
                android.R.layout.simple_spinner_item, repositoryLabels);
        repositoryAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        repository.setAdapter(repositoryAdapter);
        repository.setSelection(repositorySelection);
        repository.setContentDescription("Repository log filter");
        filters.addView(label("Repository"));
        filters.addView(repository);
        filters.addView(ui.text("This view is saved automatically and restored when Logs opens again.",
                UiTypography.Role.CAPTION, ui.palette.muted));
        filters.addView(ui.secondaryButton("Reset saved view", v -> {
            AppSettings.resetLogView(this);
            historyQuery = "";
            historyStatus = "all";
            historyProject = "all";
            historySeverity = "all";
            historyRepositoryId = "";
            rebuildUi();
        }));
        content.addView(filters);

        content.addView(ui.sectionTitle("Runs"));
        LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        content.addView(list);
        Runnable refresh = () -> scheduleHistoryListRefresh(list, search.getText().toString(),
                String.valueOf(status.getSelectedItem()), String.valueOf(project.getSelectedItem()),
                String.valueOf(severity.getSelectedItem()), historyRepositoryId);
        search.addTextChangedListener(new TextWatcher() {
            public void beforeTextChanged(CharSequence value, int start, int count, int after) {}
            public void onTextChanged(CharSequence value, int start, int before, int count) {
                historyQuery = value.toString();
                saveLogView();
                historyVisibleLimit = HISTORY_PAGE_SIZE;
                refresh.run();
            }
            public void afterTextChanged(Editable value) {}
        });
        status.setOnItemSelectedListener(new SimpleItemSelectedListener(() -> {
            historyStatus = String.valueOf(status.getSelectedItem()); saveLogView(); historyVisibleLimit = HISTORY_PAGE_SIZE; refresh.run();
        }));
        project.setOnItemSelectedListener(new SimpleItemSelectedListener(() -> {
            historyProject = String.valueOf(project.getSelectedItem()); saveLogView(); historyVisibleLimit = HISTORY_PAGE_SIZE; refresh.run();
        }));
        severity.setOnItemSelectedListener(new SimpleItemSelectedListener(() -> {
            historySeverity = String.valueOf(severity.getSelectedItem()); saveLogView(); historyVisibleLimit = HISTORY_PAGE_SIZE; refresh.run();
        }));
        repository.setOnItemSelectedListener(new SimpleItemSelectedListener(() -> {
            int selected = repository.getSelectedItemPosition();
            historyRepositoryId = selected >= 0 && selected < repositoryIds.size() ? repositoryIds.get(selected) : "";
            saveLogView();
            historyVisibleLimit = HISTORY_PAGE_SIZE;
            refresh.run();
        }));
        refresh.run();

        LinearLayout actions = ui.surface();
        actions.addView(ui.secondaryButton("Clear indexed run history", v -> new AlertDialog.Builder(this)
                .setTitle("Clear all indexed runs?")
                .setMessage("This removes Android run and structured-entry records. Repository files, Git history, artifacts and external Devtool logs are not deleted.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Clear", (dialog, which) -> { RunStore.clear(this); rebuildUi(); })
                .show()));
        content.addView(actions);
    }

    private void buildRepositoriesPage(LinearLayout content) {
        content.addView(ui.sectionTitle("Repository sources"));
        LinearLayout controls = ui.surface();
        controls.addView(ui.text("Repositories are opt-in. Active Devtool runs appear temporarily as LIVE · NOT SAVED until you save them.",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        if (!repositoryError.isEmpty()) controls.addView(ui.text(repositoryError, UiTypography.Role.BODY_SMALL, ui.palette.red));
        controls.addView(ui.button(RepositoryStore.busy() ? "Refreshing…" : "Refresh saved and live",
                ui.palette.cyan, v -> RepositoryStore.refreshAsync(this, false)));
        controls.addView(ui.secondaryButton("Discover Termux and chroot repositories",
                v -> RepositoryStore.refreshAsync(this, true)));
        controls.addView(ui.secondaryButton("Add repository…", v -> openRepositoryPicker()));
        content.addView(controls);

        content.addView(ui.sectionTitle("Search and filters"));
        LinearLayout filters = ui.surface();
        EditText search = ui.input(repositoryQuery, "Search name, path, branch, remote or environment", false);
        Spinner filter = spinner(new String[]{"all", "saved", "modified", "clean", "live", "missing", "termux", "chroot"},
                repositoryFilter, "Repository filter");
        filters.addView(search);
        filters.addView(label("Filter"));
        filters.addView(filter);
        content.addView(filters);

        content.addView(ui.sectionTitle("Repositories"));
        LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        content.addView(list);
        Runnable refresh = () -> populateRepositoryList(list,
                RepositoryStore.filter(RepositoryStore.all(this), search.getText().toString(),
                        String.valueOf(filter.getSelectedItem())));
        search.addTextChangedListener(new TextWatcher() {
            public void beforeTextChanged(CharSequence value, int start, int count, int after) {}
            public void onTextChanged(CharSequence value, int start, int before, int count) {
                repositoryQuery = value.toString(); refresh.run();
            }
            public void afterTextChanged(Editable value) {}
        });
        filter.setOnItemSelectedListener(new SimpleItemSelectedListener(() -> {
            repositoryFilter = String.valueOf(filter.getSelectedItem()); refresh.run();
        }));
        refresh.run();
    }

    private void populateRepositoryList(LinearLayout list, List<RepositoryRecord> repositories) {
        list.removeAllViews();
        if (repositories.isEmpty()) {
            LinearLayout empty = ui.surface();
            empty.addView(ui.text("No repositories match", UiTypography.Role.CARD_TITLE, ui.palette.text));
            empty.addView(ui.text("Add a path explicitly, discover repositories with root, or start a Devtool run to create a live entry.",
                    UiTypography.Role.BODY_SMALL, ui.palette.muted));
            empty.addView(ui.button("Add repository", ui.palette.cyan, v -> openRepositoryPicker()));
            list.addView(empty);
            return;
        }
        for (RepositoryRecord repository : repositories) list.addView(repositoryCard(repository));
    }

    private View repositoryCard(RepositoryRecord repository) {
        LinearLayout card = ui.surface();
        card.setContentDescription(repository.name() + ", " + repository.status + ", "
                + repository.location().displayEnvironment() + ", " + repository.canonicalPath);
        LinearLayout heading = new LinearLayout(this);
        heading.setOrientation(LinearLayout.HORIZONTAL);
        heading.addView(ui.text(repository.name(), UiTypography.Role.CARD_TITLE, ui.palette.text),
                new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        heading.addView(ui.text(repository.status.toUpperCase(Locale.ROOT), UiTypography.Role.CAPTION,
                repositoryStatusColor(repository.status)));
        card.addView(heading);
        TextView path = ui.text(repository.canonicalPath, UiTypography.Role.BODY_SMALL, ui.palette.muted);
        path.setTypeface(Typeface.MONOSPACE);
        path.setMaxLines(2);
        card.addView(path);
        long activeGitOperations = RepositoryWorkspaceStore.operations(this, repository.repositoryId).stream()
                .filter(RepositoryOperationRecord::active).count();
        card.addView(ui.text(join(" · ", repository.branch.isEmpty() ? "No branch" : repository.branch,
                        repository.location().displayEnvironment(), repository.linuxUser,
                        repository.projectType, repository.saved ? "SAVED" : "LIVE · NOT SAVED",
                        repository.activeRunCount > 0 ? repository.activeRunCount + " live" : "",
                        activeGitOperations > 0 ? activeGitOperations + " Git op" + (activeGitOperations == 1 ? "" : "s") : ""),
                UiTypography.Role.CAPTION, activeGitOperations > 0 ? ui.palette.cyan
                        : repository.saved ? ui.palette.muted : ui.palette.yellow));
        if (repository.aheadCount > 0 || repository.behindCount > 0) {
            card.addView(ui.text(repository.aheadCount + " ahead · " + repository.behindCount + " behind",
                    UiTypography.Role.CAPTION, ui.palette.cyan));
        }
        if (!repository.lastError.isEmpty()) card.addView(ui.text(repository.lastError,
                UiTypography.Role.BODY_SMALL, ui.palette.red));
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(ui.verticalActions() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        Button open = ui.button("Open", ui.palette.cyan, v -> openRepository(repository.repositoryId));
        Button use = ui.secondaryButton("Use in Run", v -> useRepositoryInRun(repository));
        Button save = ui.secondaryButton(repository.saved ? "Remove" : "Save", v -> {
            if (repository.saved) RepositoryStore.remove(this, repository.repositoryId);
            else RepositoryStore.setSaved(this, repository.repositoryId, true);
        });
        if (ui.verticalActions()) { actions.addView(open); actions.addView(use); actions.addView(save); }
        else {
            actions.addView(open, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            actions.addView(use, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            actions.addView(save, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        }
        card.addView(actions);
        return card;
    }

    private void openRepository(String repositoryId) {
        boolean hasOperation = RepositoryWorkspaceStore.operations(this, repositoryId).stream()
                .anyMatch(RepositoryOperationRecord::active);
        startActivity(new Intent(this, RepositoryDetailActivity.class)
                .putExtra(RepositoryDetailActivity.EXTRA_REPOSITORY_ID, repositoryId)
                .putExtra(RepositoryDetailActivity.EXTRA_TAB, hasOperation ? 4 : 0));
    }

    private void openRepositoryPicker() {
        startActivity(new Intent(this, RepositoryPickerActivity.class)
                .putExtra(RepositoryPickerActivity.EXTRA_MODE, RepositoryPickerActivity.MODE_ADD));
    }

    private void useRepositoryInRun(RepositoryRecord repository) {
        DevtoolRunConfig config = AppSettings.runConfig(this);
        config.repo = repository.canonicalPath;
        config.workdir = repository.environment.equals("chroot") || repository.environment.equals("root") ? "~/"
                : (repository.hostPath.isEmpty() ? repository.canonicalPath : repository.hostPath);
        config.projectType = repository.projectType.isEmpty() ? "auto" : repository.projectType;
        config.executionEnvironment = repository.environment.equals("chroot") ? "chroot"
                : repository.environment.equals("root") ? "android-device" : "native-termux";
        config.useChroot = repository.environment.equals("chroot");
        config.useRootBridge = repository.environment.equals("root") || config.useRootBridge;
        config.chrootName = repository.chrootName.isEmpty() ? "androidos" : repository.chrootName;
        AppSettings.setRunConfig(this, config);
        AppSettings.setRunDraft(this, config);
        RepositoryStore.touch(this, repository.repositoryId);
        switchPage(1);
    }

    private void buildSettingsPage(LinearLayout content) {
        content.addView(ui.sectionTitle("Appearance"));
        LinearLayout appearance = ui.surface();
        Switch monet = ui.toggle("Use Android Monet colors", AppSettings.useMonet(this));
        monet.setOnCheckedChangeListener((button, checked) -> {
            AppSettings.setUseMonet(this, checked);
            rebuildUi();
        });
        appearance.addView(monet);
        appearance.addView(ui.text("Off uses the shared Devtool desktop palette. On follows wallpaper colors on Android 12+.", UiTypography.Role.BODY_SMALL, ui.palette.muted));
        content.addView(appearance);

        buildRetentionSettings(content);

        content.addView(ui.sectionTitle("Permissions and integrations"));
        addCoreTiles(content, true);
        content.addView(ui.sectionTitle("Optional root bridge"));
        content.addView(rootBridgeTile());
        if (Build.VERSION.SDK_INT >= 36) content.addView(promotedTile());

        content.addView(ui.sectionTitle("Diagnostics"));
        content.addView(ui.quickTile(R.drawable.ic_notification_status, "Test notification", "Send a sample progress notification", ui.palette.green,
                v -> new NotificationController(this).post(HudUpdate.sample())));
        content.addView(ui.quickTile(R.drawable.ic_hud, "Test floating HUD", "Show a sample overlay card", ui.palette.cyan, v -> testOverlay()));
        content.addView(ui.quickTile(R.drawable.ic_terminal, "Open Termux", "Bring the current Termux session to the foreground", ui.palette.cyan,
                v -> { if (!TermuxCommandRunner.openTermux(this)) Toast.makeText(this, "Termux could not be opened", Toast.LENGTH_LONG).show(); }));
        int bridgeProbeColor = BridgeRoundtripProbe.state(this).equals("passed") ? ui.palette.green
                : (BridgeRoundtripProbe.state(this).equals("failed") ? ui.palette.red : ui.palette.cyan);
        content.addView(ui.quickTile(R.drawable.ic_link, "Bridge roundtrip probe",
                BridgeRoundtripProbe.label(this) + " · " + BridgeRoundtripProbe.detail(this),
                bridgeProbeColor, v -> bridgeRoundtripAction()));
        content.addView(ui.quickTile(R.drawable.ic_tracker, "Debug Workbench",
                "Health, run trace, bridge probe, repo doctor, queue recovery and recent events",
                ui.palette.yellow, v -> openDebugWorkbench()));
        buildSupportBundleSettings(content);

        content.addView(ui.sectionTitle("Command runner"));
        content.addView(ui.text("Advanced raw commands always use the rootless Termux backend. The optional root bridge accepts only visual-builder devtool workflows.", UiTypography.Role.BODY_SMALL, ui.palette.muted));
        LinearLayout runner = ui.surface();
        rawCommandInput = ui.input(AppSettings.command(this), "Raw devtool command", true);
        workdirInput = ui.input(AppSettings.workdir(this), "Termux working directory", false);
        foregroundToggle = ui.toggle("Open Termux in foreground while command runs", !AppSettings.runInBackground(this));
        overlayDefaultToggle = ui.toggle("Request floating HUD by default", AppSettings.overlayByDefault(this));
        runner.addView(rawCommandInput);
        runner.addView(ui.secondaryButton("Paste command from clipboard", v -> pasteRawCommandFromClipboard()));
        runner.addView(workdirInput);
        runner.addView(foregroundToggle);
        runner.addView(ui.text("When disabled, Termux runs in the background and the notification/log view becomes the primary progress surface.", UiTypography.Role.LABEL, ui.palette.muted));
        runner.addView(overlayDefaultToggle);
        runner.addView(ui.button("Save runner settings", ui.palette.green, v -> saveCommandSettings()));
        runner.addView(ui.secondaryButton("Run raw command", v -> runConfiguredCommand()));
        content.addView(runner);

        content.addView(ui.sectionTitle("Secure bridge"));
        LinearLayout bridge = ui.surface();
        tokenView = ui.text(BridgeSecrets.getOrCreateToken(this), UiTypography.Role.BODY_SMALL, ui.palette.text);
        tokenView.setTextIsSelectable(true);
        tokenView.setTypeface(Typeface.MONOSPACE);
        tokenView.setPadding(ui.dp(12), ui.dp(10), ui.dp(12), ui.dp(10));
        tokenView.setBackground(ui.flat(ui.palette.surfaceRaised, 10));
        bridge.addView(tokenView);
        bridge.addView(ui.secondaryButton("Copy bridge token", v -> copyToken()));
        bridge.addView(ui.secondaryButton("Regenerate bridge token", v -> {
            tokenView.setText(BridgeSecrets.regenerateToken(this));
            Toast.makeText(this, "Token regenerated", Toast.LENGTH_LONG).show();
            rebuildUi();
        }));
        content.addView(bridge);
    }

    private void buildSupportBundleSettings(LinearLayout content) {
        LinearLayout bundle = ui.surface();
        bundle.addView(ui.text("Support bundle", UiTypography.Role.CARD_TITLE, ui.palette.text));
        bundle.addView(ui.text("Copies a redacted JSON snapshot of app state, database counts, root bridge status, recent runs, repositories and queued Git operations. Raw logs are not included.",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        Switch includeCommands = ui.toggle("Include redacted recent commands", AppSettings.includeSupportCommands(this));
        includeCommands.setContentDescription("Include redacted recent command strings in support bundles");
        includeCommands.setOnCheckedChangeListener((button, checked) -> AppSettings.setIncludeSupportCommands(this, checked));
        bundle.addView(includeCommands);
        bundle.addView(ui.text("Leave this off when sharing publicly. When enabled, command text is still redacted for bridge tokens, HTTPS credentials, authorization headers and common secret assignments.",
                UiTypography.Role.LABEL, ui.palette.muted));
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(ui.verticalActions() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        Button copy = ui.button("Copy support bundle", ui.palette.green,
                v -> copyText("Devtool support bundle", SupportBundle.build(this)));
        Button share = ui.secondaryButton("Share support bundle", v -> shareSupportBundle());
        if (ui.verticalActions()) {
            actions.addView(copy, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
            actions.addView(share, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
        } else {
            actions.addView(copy, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            actions.addView(share, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        }
        bundle.addView(actions);
        content.addView(bundle);
    }

    private void buildRetentionSettings(LinearLayout content) {
        content.addView(ui.sectionTitle("Indexed data retention"));
        DevtoolDatabase.DatabaseStats stats = DevtoolDatabase.get(this).databaseStats();
        LinearLayout retention = ui.surface();
        retention.addView(ui.text(stats.runs + " runs · " + stats.pinnedRuns + " pinned · "
                        + stats.logEntries + " structured entries",
                UiTypography.Role.CARD_TITLE, ui.palette.text));
        retention.addView(ui.text(stats.repositories + " repositories · " + stats.operations
                        + " Git operations · " + formatBytes(stats.databaseBytes),
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        retention.addView(ui.text("Cleanup affects only Android-indexed metadata. Authoritative Devtool logs, repositories, artifacts, Git history, and untracked files are never deleted.",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));

        String[] runDaysValues = {"7", "14", "30", "60", "90", "365"};
        String[] maxRunValues = {"100", "250", "500", "1000", "2000"};
        String[] operationDaysValues = {"7", "30", "90", "365"};
        String[] eventDaysValues = {"30", "90", "180", "365"};
        Spinner runDays = spinner(runDaysValues, String.valueOf(AppSettings.completedRunRetentionDays(this)),
                "Completed run retention days");
        Spinner maxRuns = spinner(maxRunValues, String.valueOf(AppSettings.maximumCompletedRuns(this)),
                "Maximum completed runs");
        Spinner operationDays = spinner(operationDaysValues,
                String.valueOf(AppSettings.repositoryOperationRetentionDays(this)),
                "Git operation retention days");
        Spinner eventDays = spinner(eventDaysValues,
                String.valueOf(AppSettings.repositoryEventRetentionDays(this)),
                "Repository activity retention days");
        retention.addView(label("Completed runs: days"));
        retention.addView(runDays);
        retention.addView(label("Maximum completed runs"));
        retention.addView(maxRuns);
        retention.addView(label("Completed Git operations: days"));
        retention.addView(operationDays);
        retention.addView(label("Repository activity: days"));
        retention.addView(eventDays);
        retention.addView(ui.button("Save retention policy", ui.palette.green, v -> {
            AppSettings.setRetentionPolicy(this,
                    Integer.parseInt(String.valueOf(runDays.getSelectedItem())),
                    Integer.parseInt(String.valueOf(maxRuns.getSelectedItem())),
                    Integer.parseInt(String.valueOf(operationDays.getSelectedItem())),
                    Integer.parseInt(String.valueOf(eventDays.getSelectedItem())));
            Toast.makeText(this, "Retention policy saved", Toast.LENGTH_SHORT).show();
        }));
        retention.addView(ui.secondaryButton("Clean indexed data now", v -> runRetention(false)));
        retention.addView(ui.secondaryButton("Clean and compact database…", v -> new AlertDialog.Builder(this)
                .setTitle("Clean and compact indexed data?")
                .setMessage("The cleanup policy will run, then SQLite will reclaim unused database space. Active and pinned runs are preserved.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Clean and compact", (dialog, which) -> runRetention(true))
                .show()));
        content.addView(retention);
    }

    private void runRetention(boolean compactDatabase) {
        Toast.makeText(this, compactDatabase ? "Cleaning and compacting…" : "Cleaning indexed data…",
                Toast.LENGTH_SHORT).show();
        RetentionManager.runNow(this, compactDatabase, report -> runOnUiThread(() -> {
            Toast.makeText(this, report.summary(), Toast.LENGTH_LONG).show();
            rebuildUi();
        }));
    }

    private static String formatBytes(long value) {
        if (value < 1024L) return value + " B";
        double kib = value / 1024.0;
        if (kib < 1024.0) return String.format(Locale.ROOT, "%.1f KiB", kib);
        double mib = kib / 1024.0;
        if (mib < 1024.0) return String.format(Locale.ROOT, "%.1f MiB", mib);
        return String.format(Locale.ROOT, "%.2f GiB", mib / 1024.0);
    }

    private Spinner spinner(String[] values, String selected, String accessibilityLabel) {
        Spinner spinner = new Spinner(this);
        ArrayAdapter<String> adapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, values);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        spinner.setAdapter(adapter); spinner.setSelection(indexOf(values, selected));
        spinner.setBackground(ui.flat(ui.palette.surfaceRaised, 10)); spinner.setPadding(ui.dp(8), ui.dp(4), ui.dp(8), ui.dp(4));
        spinner.setMinimumHeight(ui.dp(48));
        spinner.setContentDescription(accessibilityLabel);
        return spinner;
    }

    private LinearLayout progressiveSection(LinearLayout parent, String label, String showText, String hideText, Runnable toggleState, boolean initiallyOpen) {
        LinearLayout section = new LinearLayout(this);
        section.setOrientation(LinearLayout.VERTICAL);
        section.setPadding(ui.dp(10), ui.dp(8), ui.dp(10), ui.dp(8));
        section.setBackground(ui.flat(ui.palette.surfaceRaised, 10));
        Button toggle = ui.secondaryButton(initiallyOpen ? hideText : showText, v -> {
            boolean opening = section.getVisibility() != View.VISIBLE;
            section.setVisibility(opening ? View.VISIBLE : View.GONE);
            ((Button) v).setText(opening ? hideText : showText);
            toggleState.run();
        });
        toggle.setContentDescription(label + ", " + (initiallyOpen ? "expanded" : "collapsed"));
        parent.addView(toggle);
        section.setVisibility(initiallyOpen ? View.VISIBLE : View.GONE);
        parent.addView(section);
        return section;
    }

    private boolean selectedRunNeedsTests() {
        if (operationInput == null) return true;
        String operation = String.valueOf(operationInput.getSelectedItem());
        boolean apply = operation.equals("apply-overlay") || operation.equals("apply-overlays");
        return operation.equals("test") || operation.equals("validate") || apply;
    }

    private int visibleRunStepCount() { return selectedRunNeedsTests() ? 4 : 3; }

    private int visibleRunStepIndex() { return selectedRunNeedsTests() ? runStage : (runStage < 2 ? runStage : 2); }

    private String runStageName(int stage) {
        if (stage == 0) return "Project";
        if (stage == 1) return "Operation";
        if (stage == 2) return "Tests";
        return "Review";
    }

    private View buildRunActionBar() {
        LinearLayout bar=new LinearLayout(this);bar.setOrientation(ui.verticalActions()?LinearLayout.VERTICAL:LinearLayout.HORIZONTAL);bar.setGravity(Gravity.CENTER_VERTICAL);bar.setPadding(ui.dp(12),ui.dp(8),ui.dp(12),ui.dp(8));bar.setBackgroundColor(ui.palette.surface);
        runBackButton=ui.secondaryButton("Back",v->showRunStage(runStage-1));runStageLabel=ui.text("", UiTypography.Role.LABEL, ui.palette.muted);runStageLabel.setGravity(Gravity.CENTER);
        runNextButton=ui.secondaryButton("Next",v->showRunStage(runStage+1));runExecuteButton=ui.button("Run devtool",ui.palette.green,v->requestRunBuiltCommand());
        runBackButton.setContentDescription("Previous workflow step"); runNextButton.setContentDescription("Next workflow step"); runExecuteButton.setContentDescription("Run reviewed devtool workflow");
        if(ui.verticalActions()){
            bar.addView(runStageLabel,new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,LinearLayout.LayoutParams.WRAP_CONTENT));
            bar.addView(runBackButton,new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,LinearLayout.LayoutParams.WRAP_CONTENT));
            bar.addView(runNextButton,new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,LinearLayout.LayoutParams.WRAP_CONTENT));
            bar.addView(runExecuteButton,new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,LinearLayout.LayoutParams.WRAP_CONTENT));
        }else{
            bar.addView(runBackButton,new LinearLayout.LayoutParams(0,LinearLayout.LayoutParams.WRAP_CONTENT,1f));bar.addView(runStageLabel,new LinearLayout.LayoutParams(0,LinearLayout.LayoutParams.WRAP_CONTENT,1.4f));bar.addView(runNextButton,new LinearLayout.LayoutParams(0,LinearLayout.LayoutParams.WRAP_CONTENT,1f));bar.addView(runExecuteButton,new LinearLayout.LayoutParams(0,LinearLayout.LayoutParams.WRAP_CONTENT,1.2f));
        }
        showRunStage(runStage);return bar;
    }

    private void showRunStage(int stage) {
        runStage=Math.max(0,Math.min(3,stage));
        if(!selectedRunNeedsTests()&&runStage==2)runStage=3;
        if(runProjectSection!=null)runProjectSection.setVisibility(runStage==0?View.VISIBLE:View.GONE);if(runOperationSection!=null)runOperationSection.setVisibility(runStage==1?View.VISIBLE:View.GONE);if(runTestsSection!=null)runTestsSection.setVisibility(runStage==2?View.VISIBLE:View.GONE);if(runReviewSection!=null)runReviewSection.setVisibility(runStage==3?View.VISIBLE:View.GONE);
        if(runStageLabel!=null){String stageText="Step "+(visibleRunStepIndex()+1)+" of "+visibleRunStepCount()+"\n"+runStageName(runStage);runStageLabel.setText(stageText);runStageLabel.setContentDescription(stageText.replace("\n",", "));runStageLabel.announceForAccessibility(stageText.replace("\n",", "));}if(runBackButton!=null)runBackButton.setEnabled(runStage>0);if(runNextButton!=null){runNextButton.setVisibility(runStage<3?View.VISIBLE:View.GONE);runNextButton.setText(!selectedRunNeedsTests()&&runStage==1?"Review":"Next");}if(runExecuteButton!=null)runExecuteButton.setVisibility(runStage==3?View.VISIBLE:View.GONE);captureRunDraft();
    }

    private void updateOperationVisibility() {
        if(operationInput==null)return;String operation=String.valueOf(operationInput.getSelectedItem());boolean apply=operation.equals("apply-overlay")||operation.equals("apply-overlays");boolean commitOperation=operation.equals("commit");
        if(artifactInput!=null)artifactInput.setVisibility(apply?View.VISIBLE:View.GONE);if(commitInput!=null)commitInput.setVisibility(apply||commitOperation?View.VISIBLE:View.GONE);if(customInput!=null)customInput.setVisibility(operation.equals("custom")?View.VISIBLE:View.GONE);if(validateToggle!=null)validateToggle.setVisibility(apply?View.VISIBLE:View.GONE);if(commitToggle!=null)commitToggle.setVisibility(apply?View.VISIBLE:View.GONE);
        boolean tests=operation.equals("test")||operation.equals("validate")||apply;if(!tests&&runStage==2)showRunStage(3);
    }

    private void populateTestCatalog() {
        if(testCatalogList==null)return;testCatalogList.removeAllViews();String query=testCatalogSearch==null?"":testCatalogSearch.getText().toString();List<TestCatalog.Item> filtered=TestCatalog.filter(testCatalogItems,query);int limit=Math.min(filtered.size(),200);
        for(int i=0;i<limit;i++){TestCatalog.Item item=filtered.get(i);CheckBox check=new CheckBox(this);check.setText(item.id);check.setTextColor(ui.palette.text);UiTypography.apply(check, UiTypography.Role.BODY_SMALL);check.setMinHeight(ui.dp(48));check.setMinimumHeight(ui.dp(48));check.setContentDescription("Select test "+item.id);check.setChecked(selectedTestIds.contains(item.id));check.setOnCheckedChangeListener((button,checked)->{if(checked)selectedTestIds.add(item.id);else selectedTestIds.remove(item.id);if(testSelectorsInput!=null)testSelectorsInput.setText(String.join(", ",selectedTestIds));updatePreview();});testCatalogList.addView(check);}
        if(filtered.isEmpty())testCatalogList.addView(ui.text("No discovered tests match this search.", UiTypography.Role.LABEL, ui.palette.muted));else if(filtered.size()>limit)testCatalogList.addView(ui.text("Showing the first "+limit+" of "+filtered.size()+" matches.", UiTypography.Role.LABEL, ui.palette.muted));
    }

    private void discoverTestsOnAndroid() {
        try{DevtoolRunConfig config=readRunConfig().copy();config.operation="test";config.listTests=true;config.testSelectors="";config.testTags="";config.testExcludeTags="";config.testPath="";config.testClass="";config.testMethod="";String command=DevtoolCommandBuilder.build(config);runRaw(command,resolvedWorkdir(config),config.overlay,config.useRootBridge);Toast.makeText(this,"Test discovery started. Return to Tests after it finishes.",Toast.LENGTH_LONG).show();}catch(RuntimeException error){Toast.makeText(this,"Could not start discovery: "+error.getMessage(),Toast.LENGTH_LONG).show();}
    }

    private void scheduleHistoryListRefresh(LinearLayout parent, String query, String status, String project,
                                            String severity, String repositoryId) {
        if (pendingHistoryRefresh != null) mainHandler.removeCallbacks(pendingHistoryRefresh);
        parent.removeAllViews();
        LinearLayout loading = ui.surface();
        loading.addView(ui.text("Loading logs…", UiTypography.Role.CARD_TITLE, ui.palette.text));
        loading.addView(ui.text("Search and filters run in the background for large indexed histories.",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        parent.addView(loading);
        int generation = ++historyLoadGeneration;
        int visibleLimit = historyVisibleLimit;
        pendingHistoryRefresh = () -> historyExecutor.execute(() -> {
            Context app = getApplicationContext();
            List<RunRecord> records = RunStore.all(app);
            List<ValidationSessionRecord> validations = ValidationStore.all(app);
            String needle = query == null ? "" : query.trim().toLowerCase(Locale.ROOT);
            String wantedSeverity = severity == null ? "all" : severity.trim().toLowerCase(Locale.ROOT);
            Set<String> structuredMatches = needle.isEmpty()
                    ? java.util.Collections.emptySet() : DevtoolDatabase.get(app).matchingRunIds(needle);
            Set<String> severityMatches = wantedSeverity.equals("all")
                    ? java.util.Collections.emptySet() : DevtoolDatabase.get(app).runIdsWithSeverity(wantedSeverity);
            mainHandler.post(() -> {
                if (generation != historyLoadGeneration || isFinishing()) return;
                populateHistoryList(parent, records, validations, query, status, project, severity,
                        repositoryId, structuredMatches, severityMatches, visibleLimit);
            });
        });
        mainHandler.postDelayed(pendingHistoryRefresh, LOG_FILTER_DEBOUNCE_MS);
    }

    private void populateHistoryList(LinearLayout parent, List<RunRecord> records, List<ValidationSessionRecord> validations, String query,
                                     String status, String project, String severity,
                                     String repositoryId, Set<String> structuredMatches,
                                     Set<String> severityMatches, int visibleLimit) {
        parent.removeAllViews();
        String needle = query == null ? "" : query.trim().toLowerCase(Locale.ROOT);
        String wantedStatus = status == null ? "all" : status.trim().toLowerCase(Locale.ROOT);
        String wantedProject = project == null ? "all" : project.trim().toLowerCase(Locale.ROOT);
        String wantedSeverity = severity == null ? "all" : severity.trim().toLowerCase(Locale.ROOT);
        String wantedRepository = repositoryId == null ? "" : repositoryId.trim();
        int shown = 0;
        int matched = 0;
        for (RunRecord record : records) {
            if (!wantedRepository.isEmpty() && !wantedRepository.equals(record.repositoryId)) continue;
            if (!wantedStatus.equals("all") && !record.status.equalsIgnoreCase(wantedStatus)) continue;
            if (!wantedProject.equals("all") && !record.projectType.equalsIgnoreCase(wantedProject)) continue;
            if (!matchesSeverity(record, wantedSeverity) && !severityMatches.contains(record.runId)) continue;
            String hay = join("\n", record.title, record.repo, record.target, record.command,
                    record.summary, record.detail, record.error, record.stderr, record.stdout,
                    record.artifact, record.commitHash, record.phase).toLowerCase(Locale.ROOT);
            if (!needle.isEmpty() && !hay.contains(needle) && !structuredMatches.contains(record.runId)) continue;
            matched++;
            if (shown < visibleLimit) {
                parent.addView(compactHistoryCard(record));
                shown++;
            }
        }
        for (ValidationSessionRecord validation : validations) {
            String validationStatus = validationHistoryStatus(validation);
            if (!wantedRepository.isEmpty() && !wantedRepository.equals(validation.repositoryId)) continue;
            if (!wantedStatus.equals("all") && !validationStatus.equals(wantedStatus)) continue;
            if (!wantedProject.equals("all") && !validation.projectType.equalsIgnoreCase(wantedProject)) continue;
            if (wantedSeverity.equals("error") && validation.reportErrors <= 0 && !validation.blocked() && !validation.recoveryRequired()) continue;
            if (wantedSeverity.equals("warning") && validation.reportWarnings <= 0) continue;
            if (wantedSeverity.equals("success") && !validation.validated()) continue;
            String hay = join("\n", "validation", validation.title(), validation.repoRoot, validation.target,
                    validation.finalVerdict, validation.state, validation.lastError, validation.pausedReason,
                    validation.artifact, validation.commitHash, validation.reportPath).toLowerCase(Locale.ROOT);
            if (!needle.isEmpty() && !hay.contains(needle)) continue;
            matched++;
            if (shown < visibleLimit) {
                parent.addView(validationHistoryCard(validation));
                shown++;
            }
        }
        if (shown == 0) {
            LinearLayout empty = ui.surface();
            empty.addView(ui.text(records.isEmpty() && validations.isEmpty() ? "No logs yet" : "No runs match the current search and filters.",
                    UiTypography.Role.CARD_TITLE, ui.palette.text));
            if (records.isEmpty()) empty.addView(ui.button("Create a run", ui.palette.cyan, v -> switchPage(1)));
            parent.addView(empty);
        } else if (matched > shown) {
            parent.addView(ui.secondaryButton("Load more runs", v -> {
                historyVisibleLimit += HISTORY_PAGE_SIZE;
                scheduleHistoryListRefresh(parent, query, status, project, severity, repositoryId);
            }));
            parent.addView(ui.text("Showing " + shown + " of " + matched + " matching records.",
                    UiTypography.Role.CAPTION, ui.palette.muted));
        }
    }

    private String validationHistoryStatus(ValidationSessionRecord session) {
        if (session.validated()) return "ok";
        if (session.blocked() || session.recoveryRequired()) return "failed";
        if (session.state.startsWith("aborted-")) return "cancelled";
        for (ValidationPhaseRecord phase : session.phases) if (phase.status.equals("interrupted")) return "interrupted";
        return "waiting";
    }

    private View validationHistoryCard(ValidationSessionRecord session) {
        LinearLayout card = ui.surface();
        String verdict = session.finalVerdict.isEmpty() ? session.state : session.finalVerdict;
        card.setContentDescription(session.title() + " validation. " + verdict + ". "
                + session.completedPhases + " of " + session.phaseTotal + " phases.");
        LinearLayout heading = new LinearLayout(this);
        heading.setOrientation(LinearLayout.HORIZONTAL);
        heading.addView(ui.text(session.title() + " validation", UiTypography.Role.CARD_TITLE, ui.palette.text),
                new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        heading.addView(ui.text("VALIDATION · " + verdict.toUpperCase(Locale.ROOT), UiTypography.Role.CAPTION,
                session.validated() ? ui.palette.green : session.blocked() || session.recoveryRequired() ? ui.palette.red : ui.palette.yellow));
        card.addView(heading);
        card.addView(ui.text(join(" · ", session.repoRoot, session.target, humanProjectType(session.projectType),
                        session.cleanExecutorKind(), DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
                                .format(new Date(session.updatedAtMs))),
                UiTypography.Role.LABEL, ui.palette.muted));
        String summary = session.completedPhases + " of " + session.phaseTotal + " phases"
                + " · " + session.reportErrors + " errors · " + session.reportWarnings + " warnings";
        if (!session.commitStatus.isEmpty() && !session.commitStatus.equals("not-requested")) summary += " · commit " + session.commitStatus;
        card.addView(ui.text(summary, UiTypography.Role.BODY_SMALL, ui.palette.text));
        if (!session.lastError.isEmpty()) card.addView(ui.text(session.lastError, UiTypography.Role.BODY_SMALL, ui.palette.red));
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(ui.verticalActions() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        Button report = ui.button("Open report", ui.palette.cyan, v -> startActivity(
                new Intent(this, ValidationEvidenceActivity.class)
                        .putExtra(ValidationEvidenceActivity.EXTRA_SESSION_ID, session.sessionId)));
        Button workbench = ui.secondaryButton(session.terminal() ? "View Workbench" : "Continue validation", v -> {
            requestedValidationSessionId = session.sessionId;
            runValidationSection = true;
            AppSettings.setRunSection(this, "validation");
            switchPage(1);
        });
        Button repository = ui.secondaryButton("Repository", v -> startActivity(
                new Intent(this, RepositoryDetailActivity.class)
                        .putExtra(RepositoryDetailActivity.EXTRA_REPOSITORY_ID, session.repositoryId)));
        repository.setEnabled(!session.repositoryId.isEmpty());
        if (ui.verticalActions()) { actions.addView(report); actions.addView(workbench); actions.addView(repository); }
        else {
            actions.addView(report, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            actions.addView(workbench, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            actions.addView(repository, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        }
        card.addView(actions);
        return card;
    }

    private boolean matchesSeverity(RunRecord record, String severity) {
        if (severity.equals("all")) return true;
        if (severity.equals("error")) return record.errors > 0 || record.status.equals("failed") || !record.error.isEmpty();
        if (severity.equals("warning")) return record.warnings > 0 || !record.stderr.isEmpty();
        if (severity.equals("success")) return record.status.equals("ok");
        return !record.status.equals("failed") && record.errors == 0 && record.warnings == 0;
    }

    private View compactHistoryCard(RunRecord record) {
        LinearLayout card = ui.surface();
        card.setContentDescription(record.title + ". Status " + record.status + ". "
                + (record.summary.isEmpty() ? record.detail : record.summary));
        LinearLayout heading = new LinearLayout(this);
        heading.setOrientation(LinearLayout.HORIZONTAL);
        TextView title = ui.text(record.title, UiTypography.Role.CARD_TITLE, ui.palette.text);
        title.setMaxLines(ui.largeText() ? 2 : 1);
        heading.addView(title, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        heading.addView(ui.text((record.pinned ? "PINNED · " : "")
                        + record.status.toUpperCase(Locale.ROOT), UiTypography.Role.CAPTION,
                record.pinned ? ui.palette.yellow : statusColor(record.status)));
        card.addView(heading);
        card.addView(ui.text(join(" · ", record.repo, record.target, humanProjectType(record.projectType),
                        humanExecutionEnvironment(record.executionEnvironment),
                        DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(new Date(record.updatedAtMs))),
                UiTypography.Role.LABEL, ui.palette.muted));
        card.addView(ui.text(record.summary.isEmpty() ? record.detail : record.summary,
                UiTypography.Role.BODY_SMALL, ui.palette.text));
        card.addView(ui.text(record.errors + " errors · " + record.warnings + " warnings · "
                        + formatDuration(record.durationMs), UiTypography.Role.CAPTION, ui.palette.muted));
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(ui.verticalActions() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        Button details = ui.button("Open log", ui.palette.cyan, v -> openLog(record.runId));
        Button rerun = ui.secondaryButton("Run again", v -> runRaw(record.command, record.workdir,
                AppSettings.overlayByDefault(this), AppSettings.runConfig(this).useRootBridge));
        rerun.setEnabled(!record.command.isEmpty());
        Button repository = ui.secondaryButton("Repository", v -> startActivity(
                new Intent(this, RepositoryDetailActivity.class)
                        .putExtra(RepositoryDetailActivity.EXTRA_REPOSITORY_ID, record.repositoryId)));
        repository.setEnabled(!record.repositoryId.isEmpty());
        Button pin = ui.secondaryButton(record.pinned ? "Unpin" : "Pin", v -> {
            RunStore.setPinned(this, record.runId, !record.pinned);
            rebuildUi();
        });
        Button interrupt = ui.secondaryButton("Mark interrupted", v -> {
            RunStore.interrupted(this, record.runId, "Stopped tracking by user request.");
            Toast.makeText(this, "Run marked interrupted", Toast.LENGTH_SHORT).show();
            rebuildUi();
        });
        interrupt.setEnabled(!record.terminal());
        Button delete = ui.secondaryButton("Delete", v -> new AlertDialog.Builder(this)
                .setTitle("Delete this indexed run?")
                .setMessage((record.pinned ? "This run is pinned. " : "")
                        + "This removes Android metadata and structured entries. It does not delete repository files or authoritative external logs.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Delete", (dialog, which) -> { RunStore.remove(this, record.runId); rebuildUi(); })
                .show());
        if (ui.verticalActions()) {
            actions.addView(details); actions.addView(rerun); actions.addView(repository); actions.addView(interrupt); actions.addView(pin); actions.addView(delete);
        } else {
            actions.addView(details, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            actions.addView(rerun, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            actions.addView(repository, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            actions.addView(interrupt, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            actions.addView(pin, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            actions.addView(delete, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        }
        card.addView(actions);
        return card;
    }

    private void openLog(String runId) {
        startActivity(new Intent(this, LogViewerActivity.class).putExtra(LogViewerActivity.EXTRA_RUN_ID, runId));
    }

    @Override
    public boolean onKeyUp(int keyCode, KeyEvent event) {
        boolean ctrl = event.isCtrlPressed();
        boolean alt = event.isAltPressed();
        if (ctrl && keyCode >= KeyEvent.KEYCODE_1 && keyCode <= KeyEvent.KEYCODE_5) {
            switchPage(keyCode - KeyEvent.KEYCODE_1);
            return true;
        }
        if (currentPage == 1 && alt && keyCode == KeyEvent.KEYCODE_DPAD_LEFT) {
            showRunStage(runStage - 1);
            return true;
        }
        if (currentPage == 1 && alt && keyCode == KeyEvent.KEYCODE_DPAD_RIGHT) {
            showRunStage(runStage + 1);
            return true;
        }
        if (currentPage == 1 && ctrl && keyCode == KeyEvent.KEYCODE_ENTER && runStage == 3) {
            requestRunBuiltCommand();
            return true;
        }
        if (ctrl && keyCode == KeyEvent.KEYCODE_F) {
            if (currentPage == 1 && testCatalogSearch != null) testCatalogSearch.requestFocus();
            return true;
        }
        return super.onKeyUp(keyCode, event);
    }

    private int readinessCount() {
        int ready = 0;
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null && manager.areNotificationsEnabled()) ready++;
        if (TermuxCommandRunner.isInstalled(this)) ready++;
        if (TermuxCommandRunner.hasPermission(this)) ready++;
        if (BridgeTokenManager.isReady(this)) ready++;
        return ready;
    }

    private void addCoreTiles(LinearLayout content, boolean includeFloatingHud) {
        NotificationManager manager = getSystemService(NotificationManager.class);
        boolean notifications = manager != null && manager.areNotificationsEnabled();
        content.addView(ui.tile(R.drawable.ic_notification_status, "Notifications",
                notifications ? "Live progress and final results can be posted." : "Required for background progress updates.",
                notifications ? "Ready" : "Needs access",
                notifications ? ui.palette.green : ui.palette.yellow,
                notifications ? "Manage" : "Grant access", true,
                v -> requestNotifications()));

        boolean overlayPermission = OverlayHudService.hasPermission(this);
        content.addView(ui.tile(R.drawable.ic_hud, "Display over apps",
                overlayPermission ? "The optional floating HUD can appear above other apps." : "Required only when you use the floating HUD.",
                overlayPermission ? "Ready" : "Optional",
                overlayPermission ? ui.palette.green : ui.palette.cyan,
                overlayPermission ? "Manage" : "Grant access", true,
                v -> openOverlaySettings()));

        boolean termuxInstalled = TermuxCommandRunner.isInstalled(this);
        boolean termuxPermission = TermuxCommandRunner.hasPermission(this);
        String termuxState = !termuxInstalled ? "Missing" : (termuxPermission ? "Ready" : "Needs access");
        String termuxAction = !termuxInstalled ? "Open app info" : (termuxPermission ? "Manage" : "Grant access");
        content.addView(ui.tile(R.drawable.ic_terminal, "Termux command access",
                !termuxInstalled ? "Termux was not detected on this device."
                        : (termuxPermission ? "The frontend can start and monitor devtool commands."
                        : "Allow this app to invoke Termux RunCommandService."),
                termuxState,
                termuxPermission && termuxInstalled ? ui.palette.green : ui.palette.yellow,
                termuxAction, true,
                v -> {
                    if (!termuxInstalled) openOwnAppDetails();
                    else requestTermuxPermission();
                }));

        String bridgeState = BridgeTokenManager.state(this);
        boolean bridgeReady = bridgeState.equals("connected");
        boolean bridgeWorking = bridgeState.equals("working");
        content.addView(ui.tile(R.drawable.ic_link, "Termux bridge token",
                BridgeTokenManager.detail(this), bridgeStateLabel(bridgeState),
                bridgeReady ? ui.palette.green : (bridgeState.equals("outdated") ? ui.palette.yellow : ui.palette.cyan),
                BridgeTokenManager.actionLabel(this), !bridgeWorking && !bridgeState.equals("termux-missing"),
                v -> bridgeTokenAction()));

        if (includeFloatingHud) {
            boolean running = OverlayHudService.isRunning();
            boolean enabled = OverlayHudService.isEnabled(this);
            String state = running ? "Running" : (enabled ? "Enabled" : "Off");
            String action = running || enabled ? "Disable" : "Enable";
            content.addView(ui.tile(R.drawable.ic_hud, "Floating HUD",
                    running ? "The overlay is active and ready to show live runs."
                            : "Notifications remain the reliable background fallback.",
                    state, running ? ui.palette.green : ui.palette.cyan,
                    action, true, v -> {
                        if (running || enabled) OverlayHudService.setEnabled(this, false);
                        else enableOverlay();
                        rebuildUi();
                    }));
        }
    }

    private View rootBridgeTile() {
        String state = RootBridgeManager.state(this);
        int color = state.equals("ready") ? ui.palette.green
                : (state.equals("denied") || state.equals("error") ? ui.palette.red
                : (state.equals("unpaired") || state.equals("outdated") ? ui.palette.yellow : ui.palette.cyan));
        return ui.tile(R.drawable.ic_root, "Devtool Root Bridge", RootBridgeManager.detail(this),
                RootBridgeManager.stateLabel(this), color, RootBridgeManager.actionLabel(this),
                RootBridgeManager.actionEnabled(this), v -> rootBridgeAction());
    }

    private void rootBridgeAction() {
        String state = RootBridgeManager.state(this);
        if (state.equals("missing")) {
            exportRootModule();
        } else if (state.equals("unpaired") || state.equals("outdated")) {
            RootBridgeManager.pair(this);
        } else {
            RootBridgeManager.refresh(this);
        }
        rebuildUi();
    }

    private void exportRootModule() {
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT)
                .setType("application/zip")
                .putExtra(Intent.EXTRA_TITLE, RootBridgeManager.MODULE_ZIP_NAME)
                .addCategory(Intent.CATEGORY_OPENABLE);
        startActivityForResult(intent, REQUEST_ROOT_MODULE_EXPORT);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQUEST_ROOT_MODULE_EXPORT || resultCode != RESULT_OK
                || data == null || data.getData() == null) return;
        try (InputStream input = getResources().openRawResource(R.raw.devtool_root_bridge_module);
             OutputStream output = getContentResolver().openOutputStream(data.getData(), "w")) {
            if (output == null) throw new IllegalStateException("Unable to open selected destination");
            byte[] buffer = new byte[32 * 1024];
            int count;
            while ((count = input.read(buffer)) >= 0) output.write(buffer, 0, count);
            output.flush();
            Toast.makeText(this, "Module ZIP exported. Install it in your root manager and reboot.", Toast.LENGTH_LONG).show();
        } catch (Exception error) {
            Toast.makeText(this, "Module export failed: " + error.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private View promotedTile() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        boolean ready = manager != null && canPostPromotedNotifications(manager);
        return ui.tile(R.drawable.ic_promoted, "Promoted live updates",
                ready ? "Android may elevate eligible progress notifications." : "Optional Android 16 live-update presentation.",
                ready ? "Ready" : "Optional", ready ? ui.palette.green : ui.palette.cyan,
                "Manage", true, v -> openPromotedSettings());
    }

    private static String bridgeStateLabel(String state) {
        if (state.equals("connected")) return "Connected";
        if (state.equals("outdated")) return "Update needed";
        if (state.equals("working")) return "Checking";
        if (state.equals("permission-needed")) return "Needs access";
        if (state.equals("termux-missing")) return "Unavailable";
        return "Not installed";
    }

    private void openDebugWorkbench() {
        startActivity(new Intent(this, DebugWorkbenchActivity.class));
    }

    private void bridgeRoundtripAction() {
        String state = BridgeRoundtripProbe.state(this);
        if (state.equals("permission-needed")) {
            requestTermuxPermission();
            return;
        }
        TermuxCommandRunner.LaunchResult result = BridgeRoundtripProbe.start(this);
        Toast.makeText(this, result.started ? "Bridge roundtrip probe started" : result.error,
                result.started ? Toast.LENGTH_SHORT : Toast.LENGTH_LONG).show();
        rebuildUi();
    }

    private void bridgeTokenAction() {
        String state = BridgeTokenManager.state(this);
        if (state.equals("permission-needed")) {
            requestTermuxPermission();
            return;
        }
        if (state.equals("termux-missing")) {
            Toast.makeText(this, "Termux is not installed or not visible to the app", Toast.LENGTH_LONG).show();
            return;
        }
        TermuxCommandRunner.LaunchResult result = BridgeTokenManager.performPrimaryAction(this);
        Toast.makeText(this, result.started ? "Termux bridge check started" : result.error,
                result.started ? Toast.LENGTH_SHORT : Toast.LENGTH_LONG).show();
        rebuildUi();
    }

    private View runCard(RunRecord record, boolean actions) {
        LinearLayout card = ui.surface();
        card.setContentDescription(record.title + ". Status " + record.status + ". " + (record.summary.isEmpty() ? record.detail : record.summary));
        card.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
        LinearLayout heading = new LinearLayout(this);
        heading.setOrientation(LinearLayout.HORIZONTAL);
        heading.setGravity(Gravity.CENTER_VERTICAL);
        TextView title = ui.text(record.title, UiTypography.Role.CARD_TITLE, ui.palette.text);
        heading.addView(title, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        TextView badge = ui.text(record.status.toUpperCase(Locale.ROOT), UiTypography.Role.CAPTION, statusColor(record.status));
        badge.setPadding(ui.dp(9), ui.dp(5), ui.dp(9), ui.dp(5));
        badge.setBackground(ui.flat(ui.palette.surfaceRaised, 999));
        heading.addView(badge);
        card.addView(heading);

        String context = join(" · ", record.phase, humanProjectType(record.projectType), humanExecutionEnvironment(record.executionEnvironment), record.repo, record.target);
        if (!context.isEmpty()) {
            TextView contextView = ui.text(context, UiTypography.Role.BODY_SMALL, ui.palette.muted);
            contextView.setPadding(0, ui.dp(5), 0, 0);
            card.addView(contextView);
        }
        if (!record.detail.isEmpty()) {
            TextView detail = ui.text(record.detail, UiTypography.Role.BODY, ui.palette.text);
            detail.setPadding(0, ui.dp(8), 0, 0);
            card.addView(detail);
        }
        if (record.current >= 0 && record.total > 0) {
            String unit = record.unitLabel == null || record.unitLabel.trim().isEmpty() ? "items" : record.unitLabel.trim();
            TextView taskCount = ui.text(
                    record.current + "/" + record.total + " " + unit + " · " + record.progress + "%",
                    UiTypography.Role.LABEL,
                    ui.palette.cyan);
            taskCount.setPadding(0, ui.dp(7), 0, 0);
            taskCount.setContentDescription(record.current + " of " + record.total + " " + unit + " completed");
            card.addView(taskCount);
        }
        LinearLayout pipeline = new LinearLayout(this);
        pipeline.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams pipelineParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        pipelineParams.setMargins(0, ui.dp(12), 0, ui.dp(4));
        pipeline.setLayoutParams(pipelineParams);
        ExecutionGraphState persistedGraph = ExecutionGraphState.parse(record.executionJson);
        java.util.List<HudUpdate.Step> steps = persistedGraph.available()
                ? persistedGraph.steps()
                : HudUpdate.parseSteps(record.stepsJson, record.phase, record.status, record.progress);
        LinearLayout row = null;
        int rowCount = 0;
        int maxSegmentsPerRow = ui.largeText() ? 2 : 4;
        for (HudUpdate.Step step : steps) {
            if (row == null || rowCount == maxSegmentsPerRow) {
                row = new LinearLayout(this);
                row.setOrientation(LinearLayout.HORIZONTAL);
                pipeline.addView(row, new LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
                rowCount = 0;
            }
            LinearLayout segment = new LinearLayout(this);
            segment.setOrientation(LinearLayout.VERTICAL);
            segment.setPadding(ui.dp(2), 0, ui.dp(2), ui.dp(3));
            TextView stepLabel = ui.text(step.label.toUpperCase(java.util.Locale.ROOT), UiTypography.Role.CAPTION, stepColor(step));
            stepLabel.setGravity(Gravity.CENTER);
            stepLabel.setMaxLines(1);
            stepLabel.setEllipsize(android.text.TextUtils.TruncateAt.END);
            segment.addView(stepLabel, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
            String relationship = step.relationshipText();
            if (!relationship.isEmpty()) {
                TextView relationView = ui.text(relationship, UiTypography.Role.CAPTION, ui.palette.muted);
                relationView.setGravity(Gravity.CENTER);
                relationView.setMaxLines(2);
                relationView.setEllipsize(android.text.TextUtils.TruncateAt.END);
                segment.addView(relationView, new LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
            }
            ProgressBar stepProgress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
            stepProgress.setMax(100);
            stepProgress.setIndeterminate(step.running() && !step.progressKnown());
            stepProgress.setProgress(step.progressKnown() ? step.progress : 0);
            stepProgress.setProgressTintList(android.content.res.ColorStateList.valueOf(stepColor(step)));
            stepProgress.setContentDescription(step.label + ", " + step.status + ", "
                    + (step.progressKnown() ? step.progress + " percent" : "progress unknown")
                    + (relationship.isEmpty() ? "" : ", " + relationship));
            stepProgress.setProgressBackgroundTintList(android.content.res.ColorStateList.valueOf(ui.palette.surfaceRaised));
            segment.addView(stepProgress, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, ui.dp(6)));
            row.addView(segment, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            rowCount++;
        }
        if (row != null) {
            while (rowCount < maxSegmentsPerRow) {
                android.view.View spacer = new android.view.View(this);
                row.addView(spacer, new LinearLayout.LayoutParams(0, 1, 1f));
                rowCount++;
            }
        }
        card.addView(pipeline);

        String time = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
                .format(new Date(record.updatedAtMs));
        card.addView(ui.text(record.errors + " errors · " + record.warnings + " warnings · " + time, UiTypography.Role.LABEL, ui.palette.muted));

        if (actions) {
            if (!record.command.isEmpty()) {
                card.addView(ui.secondaryButton("Run again", v -> runRaw(record.command, record.workdir, AppSettings.overlayByDefault(this), AppSettings.runConfig(this).useRootBridge)));
                card.addView(ui.secondaryButton("Copy command", v -> copyText("Devtool command", record.command)));
            }
            if (!record.terminal() && record.runId.startsWith("root-")) {
                card.addView(ui.secondaryButton("Cancel root run", v -> {
                    if (RootBridgeManager.cancel(this, record.runId)) {
                        RunStore.cancelled(this, record.runId);
                        new NotificationController(this).cancel(record.runId);
                        OverlayHudService.cancel(record.runId);
                        Toast.makeText(this, "Cancellation requested", Toast.LENGTH_SHORT).show();
                        rebuildUi();
                    } else {
                        Toast.makeText(this, "This run cannot be cancelled remotely", Toast.LENGTH_LONG).show();
                    }
                }));
            }
            if (record.runId.startsWith("termux-")) {
                card.addView(ui.secondaryButton("Open Termux", v -> {
                    if (!TermuxCommandRunner.openTermux(this)) Toast.makeText(this, "Termux could not be opened", Toast.LENGTH_LONG).show();
                }));
            }
            if (!record.terminal()) {
                card.addView(ui.secondaryButton("Mark interrupted", v -> {
                    RunStore.interrupted(this, record.runId, "Stopped tracking by user request.");
                    Toast.makeText(this, "Run marked interrupted", Toast.LENGTH_SHORT).show();
                    rebuildUi();
                }));
            }
            if (!record.launchLog.isEmpty()) {
                TextView launchLog = ui.text(record.launchLog, UiTypography.Role.CAPTION, ui.palette.muted);
                launchLog.setTypeface(Typeface.MONOSPACE);
                launchLog.setTextIsSelectable(true);
                launchLog.setPadding(ui.dp(10), ui.dp(8), ui.dp(10), ui.dp(8));
                launchLog.setBackground(ui.flat(ui.palette.surfaceRaised, 8));
                card.addView(ui.text("Execution log", UiTypography.Role.LABEL, ui.palette.cyan));
                card.addView(launchLog);
                card.addView(ui.secondaryButton("Copy execution log", v -> copyText("Devtool execution log", record.launchLog)));
            }
            if (!record.summary.isEmpty()) card.addView(ui.secondaryButton("Copy success summary", v -> copyText("Devtool success summary", record.summary)));
            String output = join("\n", record.error, record.stderr, record.stdout);
            if (!output.isEmpty()) card.addView(ui.secondaryButton("Copy output", v -> copyText("Devtool output", output)));
        }
        return card;
    }

    private void runQuick(String operation) {
        DevtoolRunConfig config = AppSettings.runConfig(this);
        config.operation = operation;
        config.validate = false;
        config.commit = false;
        AppSettings.setRunConfig(this, config);
        try {
            runRaw(DevtoolCommandBuilder.build(config), resolvedWorkdir(config), config.overlay, config.useRootBridge);
        } catch (IllegalArgumentException error) {
            Toast.makeText(this, error.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private boolean dangerousOperation(String operation) { return operation.equals("apply-overlay")||operation.equals("apply-overlays")||operation.equals("restore")||operation.equals("clean")||operation.equals("commit"); }
    private String dangerousOperationMessage(String operation) { if(operation.startsWith("apply-"))return "This writes artifact files into the selected repository and may create a commit.";if(operation.equals("restore"))return "Restore may replace generated or working files.";if(operation.equals("clean"))return "Clean removes build outputs and generated caches.";return "This operation changes repository state."; }
    private void requestRunBuiltCommand(){try{DevtoolRunConfig config=readRunConfig();DevtoolCommandBuilder.build(config);if(!dangerousOperation(config.operation)){runBuiltCommand();return;}new AlertDialog.Builder(this).setTitle("Confirm "+config.operation).setMessage(dangerousOperationMessage(config.operation)+"\n\nRepository: "+config.repo).setNegativeButton("Cancel",null).setPositiveButton("Continue",(d,w)->runBuiltCommand()).show();}catch(IllegalArgumentException error){Toast.makeText(this,error.getMessage(),Toast.LENGTH_LONG).show();}}

    private void runBuiltCommand() {
        try {
            DevtoolRunConfig config = readRunConfig();
            String command = DevtoolCommandBuilder.build(config);
            AppSettings.setRunConfig(this, config);
            AppSettings.clearRunDraft(this);
            runDraft = config.copy();
            AppSettings.setCommand(this, command);
            String workdir = resolvedWorkdir(config);
            AppSettings.setWorkdir(this, workdir);
            if (foregroundToggle != null) AppSettings.setRunInBackground(this, !foregroundToggle.isChecked());
            runRaw(command, workdir, config.overlay, config.useRootBridge);
        } catch (IllegalArgumentException error) {
            Toast.makeText(this, error.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private String resolvedWorkdir(DevtoolRunConfig config) {
        String configured = config.workdir == null ? "" : config.workdir.trim();
        if (config.useChroot) {
            // Termux RUN_COMMAND validates its work directory before the shell
            // starts, so a chroot-only path such as /home/<user>/Code/project is
            // invalid here even though it exists after entering the chroot.
            // Keep the repository path in the devtool command and launch from
            // the Termux home unless the user supplied a Termux-visible path.
            if (configured.isEmpty() || configured.startsWith("/home/")
                    || configured.startsWith("/root/")) return "~/";
        }
        if (!configured.isEmpty()) return configured;
        return config.repo;
    }

    private void runRaw(String command, String workdir, boolean overlay, boolean useRootBridge) {
        RunCommandTrace.record(this, RunCommandTrace.UI_RUN_REQUESTED, "MainActivity", "",
                "Run button dispatched command",
                DebugRecorder.details("workdir", workdir, "overlay", overlay,
                        "use_root_bridge", useRootBridge, "background", AppSettings.runInBackground(this),
                        "command_length", command == null ? 0 : command.length()));
        TermuxCommandRunner.LaunchResult result = useRootBridge
                ? RootBridgeManager.run(this, command, workdir, overlay)
                : TermuxCommandRunner.run(this, command, workdir, AppSettings.runInBackground(this), overlay, null);
        Toast.makeText(this,
                result.started ? "Started in " + (AppSettings.runInBackground(this) ? "background" : "foreground") + " · run " + result.executionId : result.error,
                result.started ? Toast.LENGTH_SHORT : Toast.LENGTH_LONG).show();
        if (result.started) switchPage(0);
    }

    private DevtoolRunConfig readRunConfig() {
        DevtoolRunConfig config = new DevtoolRunConfig();
        config.operation = String.valueOf(operationInput.getSelectedItem());
        config.projectType = String.valueOf(projectTypeInput.getSelectedItem());
        config.executionEnvironment = String.valueOf(executionModeInput.getSelectedItem());
        config.repo = repoInput.getText().toString();
        config.workdir = runWorkdirInput.getText().toString();
        config.target = targetInput.getText().toString();
        config.artifact = artifactInput.getText().toString();
        config.commitMessage = commitInput.getText().toString();
        config.customArguments = customInput.getText().toString();
        config.environmentAssignments = environmentInput.getText().toString();
        config.extraGlobalArguments = extraGlobalInput.getText().toString();
        config.extraOperationArguments = extraOperationInput.getText().toString();
        config.chrootName = chrootInput.getText().toString();
        config.testProfile = testProfileInput.getText().toString();
        config.testSelectors = testSelectorsInput.getText().toString();
        config.testTags = testTagsInput.getText().toString();
        config.testExcludeTags = testExcludeTagsInput.getText().toString();
        config.testPath = testPathInput.getText().toString();
        config.testClass = testClassInput.getText().toString();
        config.testMethod = testMethodInput.getText().toString();
        config.useChroot = chrootToggle.isChecked();
        config.useRootBridge = rootBridgeToggle != null && rootBridgeToggle.isChecked();
        config.copy = copyToggle.isChecked();
        config.copyViaTermux = copyViaTermuxToggle.isChecked();
        config.autoHud = autoHudToggle.isChecked();
        config.validate = validateToggle.isChecked();
        config.commit = commitToggle.isChecked();
        config.assumeYes = yesToggle.isChecked();
        config.overlay = runOverlayToggle.isChecked();
        config.listTests = listTestsToggle.isChecked();
        config.instrumented = instrumentedToggle.isChecked();
        return config;
    }

    private void saveRunConfig() {
        AppSettings.setRunConfig(this, readRunConfig());
        if (foregroundToggle != null) AppSettings.setRunInBackground(this, !foregroundToggle.isChecked());
    }

    private void importBuilderCommandFromClipboard() {
        String command = clipboardText();
        if (command == null) return;
        try {
            DevtoolCommandImporter.ImportResult imported = DevtoolCommandImporter.parse(command);
            applyImportedConfig(imported.config);
            AppSettings.setWorkdir(this, imported.workdir);
            AppSettings.setCommand(this, command);
            Toast.makeText(this, imported.notice, Toast.LENGTH_LONG).show();
        } catch (IllegalArgumentException error) {
            AppSettings.setCommand(this, command);
            Toast.makeText(this, error.getMessage() + ". Saved in the raw runner instead.", Toast.LENGTH_LONG).show();
        }
    }

    private void applyImportedConfig(DevtoolRunConfig config) {
        operationInput.setSelection(indexOf(DevtoolCommandBuilder.OPERATIONS, config.operation));
        projectTypeInput.setSelection(indexOf(DevtoolCommandBuilder.PROJECT_TYPES, config.projectType));
        executionModeInput.setSelection(indexOf(DevtoolCommandBuilder.EXECUTION_ENVIRONMENTS, config.executionEnvironment));
        repoInput.setText(config.repo);
        runWorkdirInput.setText(config.workdir);
        targetInput.setText(config.target);
        artifactInput.setText(config.artifact);
        commitInput.setText(config.commitMessage);
        customInput.setText(config.customArguments);
        environmentInput.setText(config.environmentAssignments);
        extraGlobalInput.setText(config.extraGlobalArguments);
        extraOperationInput.setText(config.extraOperationArguments);
        chrootInput.setText(config.chrootName);
        testProfileInput.setText(config.testProfile);
        testSelectorsInput.setText(config.testSelectors);
        testTagsInput.setText(config.testTags);
        testExcludeTagsInput.setText(config.testExcludeTags);
        testPathInput.setText(config.testPath);
        testClassInput.setText(config.testClass);
        testMethodInput.setText(config.testMethod);
        chrootToggle.setChecked(config.useChroot);
        if (rootBridgeToggle != null) rootBridgeToggle.setChecked(config.useRootBridge && RootBridgeManager.isReady(this));
        copyToggle.setChecked(config.copy);
        copyViaTermuxToggle.setChecked(config.copyViaTermux);
        autoHudToggle.setChecked(config.autoHud);
        validateToggle.setChecked(config.validate);
        commitToggle.setChecked(config.commit);
        yesToggle.setChecked(config.assumeYes);
        runOverlayToggle.setChecked(config.overlay);
        listTestsToggle.setChecked(config.listTests);
        instrumentedToggle.setChecked(config.instrumented);
        runDraft = config.copy();
        AppSettings.setRunDraft(this, runDraft);
        updatePreview();
    }

    private void pasteRawCommandFromClipboard() {
        String command = clipboardText();
        if (command == null) return;
        rawCommandInput.setText(command);
        Toast.makeText(this, "Command pasted", Toast.LENGTH_SHORT).show();
    }

    private String clipboardText() {
        ClipboardTextReader.Result result = ClipboardTextReader.read(this);
        if (!result.ok()) {
            Toast.makeText(this, result.error, Toast.LENGTH_LONG).show();
            return null;
        }
        return result.text;
    }

    private void updatePreview() {
        if (commandPreview == null || operationInput == null) return;
        DevtoolRunConfig config = readRunConfig();
        runDraft = config.copy();
        AppSettings.setRunDraft(this, runDraft);
        try {
            commandPreview.setText(DevtoolCommandBuilder.build(config));
            commandPreview.setTextColor(ui.palette.text);
        } catch (IllegalArgumentException error) {
            commandPreview.setText(getString(R.string.configuration_issue_format, error.getMessage()));
            commandPreview.setTextColor(ui.palette.red);
        }
    }

    private void captureRunDraft() {
        if (runValidationSection) return;
        if (currentPage != 1 || operationInput == null || repoInput == null) return;
        try {
            runDraft = readRunConfig().copy();
            AppSettings.setRunDraft(this, runDraft);
        } catch (RuntimeException ignored) {
        }
    }

    private void syncExecutionControls(boolean chrootChanged) {
        if (syncingExecutionControls || executionModeInput == null || chrootToggle == null) return;
        syncingExecutionControls = true;
        try {
            String environment = String.valueOf(executionModeInput.getSelectedItem());
            if (chrootChanged && chrootToggle.isChecked() && !environment.equals("chroot")) {
                executionModeInput.setSelection(indexOf(DevtoolCommandBuilder.EXECUTION_ENVIRONMENTS, "chroot"));
                environment = "chroot";
            } else if (!chrootChanged) {
                chrootToggle.setChecked(environment.equals("chroot"));
            }
            if (!environment.equals("chroot") && chrootToggle.isChecked()) chrootToggle.setChecked(false);
            if (chrootInput != null) chrootInput.setEnabled(chrootToggle.isChecked());
            if (copyViaTermuxToggle != null) {
                copyViaTermuxToggle.setEnabled(copyToggle != null && copyToggle.isChecked());
                if (!copyViaTermuxToggle.isEnabled()) copyViaTermuxToggle.setChecked(false);
            }
        } finally {
            syncingExecutionControls = false;
        }
    }

    private EditText field(LinearLayout parent, String label, String value, String hint, boolean multiline) {
        TextView labelView = label(label);
        EditText input = ui.input(value, hint, multiline);
        ui.labelInput(labelView, input, label, hint);
        parent.addView(labelView);
        parent.addView(input);
        return input;
    }

    private TextView label(String value) {
        TextView label = ui.text(value, UiTypography.Role.LABEL, ui.palette.muted);
        label.setPadding(0, ui.dp(12), 0, 0);
        return label;
    }

    private Switch toggle(LinearLayout parent, String label, boolean value) {
        Switch toggle = ui.toggle(label, value);
        parent.addView(toggle);
        return toggle;
    }

    private void watchPreview(EditText... fields) {
        TextWatcher watcher = new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) { updatePreview(); }
            @Override public void afterTextChanged(Editable s) {}
        };
        for (EditText field : fields) field.addTextChangedListener(watcher);
    }

    private void saveCommandSettings() {
        AppSettings.setCommand(this, rawCommandInput.getText().toString());
        AppSettings.setWorkdir(this, workdirInput.getText().toString());
        AppSettings.setRunInBackground(this, foregroundToggle == null || !foregroundToggle.isChecked());
        AppSettings.setOverlayByDefault(this, overlayDefaultToggle.isChecked());
        Toast.makeText(this, "Runner settings saved", Toast.LENGTH_SHORT).show();
    }

    private void runConfiguredCommand() {
        saveCommandSettings();
        runRaw(AppSettings.command(this), AppSettings.workdir(this), AppSettings.overlayByDefault(this), false);
    }

    private void refreshStatus() {
        if (statusView == null) return;
        NotificationManager manager = getSystemService(NotificationManager.class);
        boolean notifications = manager.areNotificationsEnabled();
        String promoted = Build.VERSION.SDK_INT >= 36
                ? (canPostPromotedNotifications(manager) ? "enabled" : "not enabled")
                : "Android 16+";
        String floatingHud = OverlayHudService.isRunning()
                ? getString(R.string.floating_hud_running)
                : (OverlayHudService.isEnabled(this)
                ? getString(R.string.floating_hud_enabled)
                : getString(R.string.floating_hud_disabled));
        statusView.setText(getString(R.string.runner_status_format,
                yesNo(notifications),
                promoted,
                yesNo(OverlayHudService.hasPermission(this)),
                floatingHud,
                yesNo(TermuxCommandRunner.isInstalled(this)),
                yesNo(TermuxCommandRunner.hasPermission(this)),
                RootBridgeManager.stateLabel(this)));
        if (tokenView != null) tokenView.setText(BridgeSecrets.getOrCreateToken(this));
    }

    private int stepColor(HudUpdate.Step step) {
        if (step.failed()) return ui.palette.red;
        if (step.succeeded()) return ui.palette.green;
        if (step.running()) return ui.palette.yellow;
        return ui.palette.muted;
    }

    private int statusColor(String status) {
        if (status.equals("ok")) return ui.palette.green;
        if (status.equals("failed") || status.equals("blocked") || status.equals("cancelled") || status.equals("interrupted")) return ui.palette.red;
        if (status.equals("waiting")) return ui.palette.yellow;
        return ui.palette.cyan;
    }

    private int repositoryStatusColor(String status) {
        if (status.equals("clean")) return ui.palette.green;
        if (status.equals("modified")) return ui.palette.yellow;
        if (status.equals("checking")) return ui.palette.cyan;
        return ui.palette.red;
    }

    private static String formatDuration(long durationMs) {
        long seconds = Math.max(0L, durationMs / 1000L);
        if (seconds < 60L) return seconds + "s";
        return seconds / 60L + "m " + seconds % 60L + "s";
    }

    private void applyValidationIntent(Intent intent) {
        if (intent == null) {
            runValidationSection = "validation".equals(AppSettings.runSection(this));
            return;
        }
        String section = intent.getStringExtra("run_section");
        if (section != null) {
            runValidationSection = "validation".equals(section);
            AppSettings.setRunSection(this, runValidationSection ? "validation" : "command");
            intent.removeExtra("run_section");
        } else {
            runValidationSection = "validation".equals(AppSettings.runSection(this));
        }
        String sessionId = intent.getStringExtra(ValidationStore.EXTRA_SESSION_ID);
        if (sessionId != null && !sessionId.trim().isEmpty()) {
            requestedValidationSessionId = sessionId.trim();
            intent.removeExtra(ValidationStore.EXTRA_SESSION_ID);
        }
    }

    private void applyLogRepositoryIntent(Intent intent) {
        if (intent == null || !intent.hasExtra("logs_repository_id")) return;
        historyRepositoryId = intent.getStringExtra("logs_repository_id");
        if (historyRepositoryId == null) historyRepositoryId = "";
        intent.removeExtra("logs_repository_id");
        saveLogView();
    }

    private void saveLogView() {
        AppSettings.setLogView(this, historyQuery, historyStatus, historyProject,
                historySeverity, historyRepositoryId);
    }

    private int resolveRequestedPage(Intent intent) {
        if (intent != null && intent.hasExtra("open_page")) {
            return Math.max(0, Math.min(PAGES.length - 1, intent.getIntExtra("open_page", 0)));
        }
        if (intent != null && (intent.hasExtra("run_id") || intent.getBooleanExtra("open_history", false))) return 2;
        int saved = AppSettings.page(this);
        return Math.max(0, Math.min(PAGES.length - 1, saved));
    }

    private void maybeOpenRequestedRun(Intent intent) {
        if (intent == null) return;
        String runId = intent.getStringExtra("run_id");
        if (runId == null || runId.trim().isEmpty()) return;
        intent.removeExtra("run_id");
        startActivity(new Intent(this, LogViewerActivity.class)
                .putExtra(LogViewerActivity.EXTRA_RUN_ID, runId.trim()));
    }

    private void switchPage(int page) {
        captureRunDraft();
        currentPage = page;
        AppSettings.setPage(this, page);
        rebuildUi();
    }

    private static int indexOf(String[] values, String wanted) {
        for (int i = 0; i < values.length; i++) if (values[i].equals(wanted)) return i;
        return 0;
    }

    private static String humanProjectType(String value) {
        String type = value == null ? "auto" : value.trim().toLowerCase(java.util.Locale.ROOT);
        if (type.equals("dotnet")) return ".NET";
        if (type.equals("android")) return "Android";
        if (type.equals("gradle")) return "Gradle/JVM";
        if (type.equals("python")) return "Python";
        if (type.equals("command")) return "Custom command";
        return "Auto-detected";
    }

    private static String humanExecutionEnvironment(String value) {
        String environment = value == null ? "auto" : value.trim().toLowerCase(java.util.Locale.ROOT);
        if (environment.equals("native-termux")) return "Termux";
        if (environment.equals("chroot")) return "Chroot";
        if (environment.equals("desktop-linux")) return "Desktop Linux";
        if (environment.equals("android-device")) return "Android device";
        if (environment.equals("external-device")) return "External device";
        return "Automatic environment";
    }

    private static String yesNo(boolean value) { return value ? "ready" : "needed"; }

    private static String join(String separator, String... values) {
        StringBuilder result = new StringBuilder();
        for (String value : values) {
            String clean = value == null ? "" : value.trim();
            if (clean.isEmpty()) continue;
            if (result.length() > 0) result.append(separator);
            result.append(clean);
        }
        return result.toString();
    }

    private boolean canPostPromotedNotifications(NotificationManager manager) {
        try {
            Object value = NotificationManager.class.getMethod("canPostPromotedNotifications").invoke(manager);
            return Boolean.TRUE.equals(value);
        } catch (ReflectiveOperationException ignored) {
            return false;
        }
    }

    private void requestNotifications() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQUEST_NOTIFICATIONS);
        } else openNotificationSettings();
    }

    private void requestTermuxPermission() {
        if (!TermuxCommandRunner.hasPermission(this)) {
            requestPermissions(new String[]{TermuxCommandRunner.RUN_COMMAND_PERMISSION}, REQUEST_TERMUX);
        } else openOwnAppDetails();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        rebuildUi();
    }

    private void enableOverlay() {
        if (!OverlayHudService.hasPermission(this)) {
            openOverlaySettings();
            return;
        }
        OverlayHudService.setEnabled(this, true);
        refreshStatus();
    }

    private void testOverlay() {
        if (!OverlayHudService.hasPermission(this)) {
            openOverlaySettings();
            return;
        }
        OverlayHudService.setEnabled(this, true);
        OverlayHudService.publish(this, HudUpdate.sampleOverlay());
    }

    private void openOverlaySettings() {
        startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:" + getPackageName())));
    }

    private void openNotificationSettings() {
        startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName()));
    }

    private void openPromotedSettings() {
        Intent intent = new Intent("android.settings.APP_NOTIFICATION_PROMOTION_SETTINGS")
                .setData(Uri.parse("package:" + getPackageName()));
        try {
            startActivity(intent);
        } catch (RuntimeException ignored) {
            openNotificationSettings();
        }
    }

    private void openOwnAppDetails() {
        startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:" + getPackageName())));
    }

    private void shareSupportBundle() {
        Intent intent = new Intent(Intent.ACTION_SEND);
        intent.setType("application/json");
        intent.putExtra(Intent.EXTRA_SUBJECT, "Devtool Android support bundle");
        intent.putExtra(Intent.EXTRA_TEXT, SupportBundle.build(this));
        try {
            startActivity(Intent.createChooser(intent, "Share support bundle"));
        } catch (RuntimeException error) {
            copyText("Devtool support bundle", SupportBundle.build(this));
        }
    }

    private void copyToken() {
        copyText("Devtool Live HUD token", BridgeSecrets.getOrCreateToken(this));
    }

    private void copyText(String label, String value) {
        try {
            ClipboardManager clipboard = getSystemService(ClipboardManager.class);
            if (clipboard == null) {
                Toast.makeText(this, "Clipboard is unavailable", Toast.LENGTH_LONG).show();
                return;
            }
            clipboard.setPrimaryClip(ClipData.newPlainText(label, value));
            Toast.makeText(this, "Copied", Toast.LENGTH_SHORT).show();
        } catch (RuntimeException error) {
            Toast.makeText(this, "Could not copy to the clipboard", Toast.LENGTH_LONG).show();
        }
    }

    private static final class SimpleItemSelectedListener implements android.widget.AdapterView.OnItemSelectedListener {
        private final Runnable action;
        SimpleItemSelectedListener(Runnable action) { this.action = action; }
        @Override public void onItemSelected(android.widget.AdapterView<?> parent, View view, int position, long id) { action.run(); }
        @Override public void onNothingSelected(android.widget.AdapterView<?> parent) { action.run(); }
    }
}
