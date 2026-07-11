package io.github.mikeceead.devtoolhud;

import android.Manifest;
import android.app.Activity;
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
import android.provider.Settings;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.widget.ArrayAdapter;
import android.widget.Button;
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
import java.util.Date;
import java.util.List;

public final class MainActivity extends Activity {
    private static final int REQUEST_NOTIFICATIONS = 41;
    private static final int REQUEST_TERMUX = 42;
    private static final int REQUEST_ROOT_MODULE_EXPORT = 43;
    private static final String[] PAGES = {"Overview", "Run", "History", "Settings"};

    private UiKit ui;
    private int currentPage;
    private TextView statusView;
    private TextView tokenView;
    private TextView commandPreview;
    private Spinner operationInput;
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
    private Switch chrootToggle;
    private Switch rootBridgeToggle;
    private Switch copyToggle;
    private Switch copyViaTermuxToggle;
    private Switch autoHudToggle;
    private Switch validateToggle;
    private Switch commitToggle;
    private Switch yesToggle;
    private Switch runOverlayToggle;
    private EditText rawCommandInput;
    private EditText workdirInput;
    private Switch backgroundToggle;
    private Switch overlayDefaultToggle;
    private boolean receiverRegistered;

    private final BroadcastReceiver runUpdates = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (currentPage != 1) rebuildUi();
        }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        new NotificationController(this);
        currentPage = getIntent().hasExtra("run_id") ? 2 : AppSettings.page(this);
        rebuildUi();
    }

    @Override
    protected void onStart() {
        super.onStart();
        if (!receiverRegistered) {
            IntentFilter filter = new IntentFilter(RunStore.ACTION_UPDATED);
            filter.addAction(BridgeTokenManager.ACTION_UPDATED);
            filter.addAction(RootBridgeManager.ACTION_UPDATED);
            if (Build.VERSION.SDK_INT >= 33) registerReceiver(runUpdates, filter, Context.RECEIVER_NOT_EXPORTED);
            else registerReceiver(runUpdates, filter);
            receiverRegistered = true;
        }
    }

    @Override
    protected void onStop() {
        if (receiverRegistered) {
            unregisterReceiver(runUpdates);
            receiverRegistered = false;
        }
        super.onStop();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (ui != null && (currentPage == 0 || currentPage == 3)) rebuildUi();
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
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(ui.palette.background);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            int top;
            int bottom;
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                top = bars.top;
                bottom = bars.bottom;
            } else {
                top = insets.getSystemWindowInsetTop();
                bottom = insets.getSystemWindowInsetBottom();
            }
            view.setPadding(0, top, 0, bottom);
            return insets;
        });

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(ui.dp(20), ui.dp(18), ui.dp(20), ui.dp(14));
        header.addView(ui.text("DEVTOOL", 12, ui.palette.cyan, true));
        TextView title = ui.text(PAGES[currentPage], 30, ui.palette.text, true);
        title.setPadding(0, ui.dp(2), 0, ui.dp(2));
        header.addView(title);
        header.addView(ui.text(pageSubtitle(), 14, ui.palette.muted, false));
        root.addView(header);

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(ui.palette.background);
        scroll.addView(buildPage());
        root.addView(scroll, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        root.addView(buildBottomNavigation());
        return root;
    }

    private View buildBottomNavigation() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(ui.dp(10), ui.dp(7), ui.dp(10), ui.dp(7));
        row.setBackgroundColor(ui.palette.surface);
        String[] symbols = {"⌂", "▶", "◷", "⚙"};
        for (int index = 0; index < PAGES.length; index++) {
            final int page = index;
            Button button = ui.navButton(symbols[index], PAGES[index], index == currentPage, v -> {
                currentPage = page;
                AppSettings.setPage(this, page);
                rebuildUi();
            });
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    0, ui.dp(58), 1f);
            params.setMargins(ui.dp(2), 0, ui.dp(2), 0);
            button.setLayoutParams(params);
            row.addView(button);
        }
        return row;
    }

    private String pageSubtitle() {
        if (currentPage == 1) return "Build and run a devtool workflow without writing shell commands.";
        if (currentPage == 2) return "Review recent runs, results and captured output.";
        if (currentPage == 3) return "Manage appearance, permissions, pairing and advanced execution.";
        return "See readiness, current work and the next useful action at a glance.";
    }

    private View buildPage() {
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(ui.dp(16), ui.dp(2), ui.dp(16), ui.dp(28));
        if (currentPage == 1) buildRunPage(content);
        else if (currentPage == 2) buildHistoryPage(content);
        else if (currentPage == 3) buildSettingsPage(content);
        else buildOverviewPage(content);
        return content;
    }

    private void buildOverviewPage(LinearLayout content) {
        content.addView(ui.sectionTitle("Readiness"));
        LinearLayout readiness = ui.surface();
        int ready = readinessCount();
        readiness.addView(ui.text(ready + " of 4 core services ready", 19, ready == 4 ? ui.palette.green : ui.palette.text, true));
        TextView readinessDetail = ui.text(
                ready == 4 ? "Android, Termux and the secure HUD bridge are connected."
                        : "Complete the highlighted tiles below before relying on live background progress.",
                13, ui.palette.muted, false);
        readinessDetail.setPadding(0, ui.dp(5), 0, 0);
        readiness.addView(readinessDetail);
        content.addView(readiness);

        addCoreTiles(content, false);
        content.addView(ui.sectionTitle("Optional root access"));
        content.addView(rootBridgeTile());

        content.addView(ui.sectionTitle("Latest run"));
        RunRecord latest = RunStore.latest(this);
        if (latest == null) {
            LinearLayout empty = ui.surface();
            empty.addView(ui.text("No devtool runs yet", 18, ui.palette.text, true));
            empty.addView(ui.text("Open Run to build a command visually or use the advanced raw-command launcher.", 14, ui.palette.muted, false));
            empty.addView(ui.button("Create a run", ui.palette.cyan, v -> switchPage(1)));
            content.addView(empty);
        } else {
            content.addView(runCard(latest, true));
        }

        content.addView(ui.sectionTitle("Quick actions"));
        content.addView(ui.quickTile("◎", "Repository status", "Inspect the configured repository", ui.palette.green, v -> runQuick("status")));
        content.addView(ui.quickTile("✓", "Validate target", "Restore, build and test efficiently", ui.palette.cyan, v -> runQuick("validate")));
        content.addView(ui.quickTile("＋", "Build a workflow", "Apply overlays, test or commit", ui.palette.yellow, v -> switchPage(1)));
        content.addView(ui.quickTile("◷", "Run history", "Review output and rerun commands", ui.palette.muted, v -> switchPage(2)));

        List<RunRecord> records = RunStore.all(this);
        if (records.size() > 1) {
            content.addView(ui.sectionTitle("Recent"));
            for (int i = 1; i < Math.min(records.size(), 4); i++) content.addView(runCard(records.get(i), false));
        }
    }

    private void buildRunPage(LinearLayout content) {
        DevtoolRunConfig saved = AppSettings.runConfig(this);

        content.addView(ui.sectionTitle("Workflow"));
        LinearLayout workflow = ui.surface();
        workflow.addView(ui.button("Paste and import command", ui.palette.cyan, v -> importBuilderCommandFromClipboard()));
        workflow.addView(ui.text("Understands multiline shell commands, leading cd, environment assignments, quoted paths and normal devtool flag ordering.", 13, ui.palette.muted, false));
        operationInput = new Spinner(this);
        ArrayAdapter<String> operations = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, DevtoolCommandBuilder.OPERATIONS);
        operations.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        operationInput.setAdapter(operations);
        operationInput.setSelection(indexOf(DevtoolCommandBuilder.OPERATIONS, saved.operation));
        operationInput.setBackground(ui.flat(ui.palette.surfaceRaised, 10));
        operationInput.setPadding(ui.dp(8), ui.dp(4), ui.dp(8), ui.dp(4));
        workflow.addView(label("Operation"));
        workflow.addView(operationInput);

        repoInput = field(workflow, "Repository", saved.repo, "Termux or chroot repository path", false);
        targetInput = field(workflow, "Target", saved.target, "Configured devtool target", false);
        artifactInput = field(workflow, "Overlay / bundle", saved.artifact, "$HOME/Downloads/example_overlay.zip", false);
        commitInput = field(workflow, "Commit message", saved.commitMessage, "Applied after successful validation", false);
        customInput = field(workflow, "Custom subcommand and arguments", saved.customArguments, "Used only when operation is custom", true);
        content.addView(workflow);

        content.addView(ui.sectionTitle("Execution"));
        LinearLayout execution = ui.surface();
        rootBridgeToggle = toggle(execution, "Use optional root bridge", saved.useRootBridge);
        boolean rootBridgeReady = RootBridgeManager.isReady(this);
        rootBridgeToggle.setEnabled(rootBridgeReady);
        if (!rootBridgeReady) rootBridgeToggle.setChecked(false);
        chrootToggle = toggle(execution, "Run through androidos chroot", saved.useChroot);
        chrootInput = field(execution, "Chroot name", saved.chrootName, "androidos", false);
        runWorkdirInput = field(execution, "Termux working directory", saved.workdir, "Auto: repository, or ~/ for chroot", false);
        copyToggle = toggle(execution, "Copy result", saved.copy);
        copyViaTermuxToggle = toggle(execution, "Use Termux clipboard route", saved.copyViaTermux);
        autoHudToggle = toggle(execution, "Live Android progress", saved.autoHud);
        validateToggle = toggle(execution, "Validate after apply", saved.validate);
        commitToggle = toggle(execution, "Commit after apply", saved.commit);
        yesToggle = toggle(execution, "Non-interactive --yes", saved.assumeYes);
        runOverlayToggle = toggle(execution, "Show floating HUD", saved.overlay);
        content.addView(execution);

        content.addView(ui.sectionTitle("Advanced"));
        LinearLayout advanced = ui.surface();
        environmentInput = field(advanced, "Environment assignments", saved.environmentAssignments, "DEVTOOL_HUD_SINGLETON=kill", false);
        extraGlobalInput = field(advanced, "Additional global arguments", saved.extraGlobalArguments, "Preserved flags before the subcommand", true);
        extraOperationInput = field(advanced, "Additional operation arguments", saved.extraOperationArguments, "Preserved arguments after the subcommand", true);
        content.addView(advanced);

        content.addView(ui.sectionTitle("Command preview"));
        LinearLayout preview = ui.surface();
        commandPreview = ui.text("", 13, ui.palette.text, false);
        commandPreview.setTypeface(Typeface.MONOSPACE);
        commandPreview.setTextIsSelectable(true);
        commandPreview.setPadding(ui.dp(12), ui.dp(10), ui.dp(12), ui.dp(10));
        commandPreview.setBackground(ui.flat(ui.palette.surfaceRaised, 10));
        preview.addView(commandPreview);
        preview.addView(ui.button("Run devtool", ui.palette.green, v -> runBuiltCommand()));
        preview.addView(ui.secondaryButton("Copy command", v -> copyText("Devtool command", commandPreview.getText().toString())));
        preview.addView(ui.secondaryButton("Save as defaults", v -> {
            saveRunConfig();
            Toast.makeText(this, "Run defaults saved", Toast.LENGTH_SHORT).show();
        }));
        content.addView(preview);

        operationInput.setOnItemSelectedListener(new SimpleItemSelectedListener(this::updatePreview));
        watchPreview(repoInput, runWorkdirInput, targetInput, artifactInput, commitInput, customInput, chrootInput, environmentInput, extraGlobalInput, extraOperationInput);
        for (Switch value : new Switch[]{rootBridgeToggle, chrootToggle, copyToggle, copyViaTermuxToggle, autoHudToggle, validateToggle, commitToggle, yesToggle, runOverlayToggle}) {
            value.setOnCheckedChangeListener((button, checked) -> updatePreview());
        }
        updatePreview();
    }

    private void buildHistoryPage(LinearLayout content) {
        List<RunRecord> records = RunStore.all(this);
        content.addView(ui.sectionTitle("Runs"));
        if (records.isEmpty()) {
            LinearLayout empty = ui.surface();
            empty.addView(ui.text("No history yet", 18, ui.palette.text, true));
            empty.addView(ui.button("Create a run", ui.palette.cyan, v -> switchPage(1)));
            content.addView(empty);
            return;
        }
        for (RunRecord record : records) content.addView(runCard(record, true));
        LinearLayout actions = ui.surface();
        actions.addView(ui.secondaryButton("Clear run history", v -> {
            RunStore.clear(this);
            rebuildUi();
        }));
        content.addView(actions);
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
        appearance.addView(ui.text("Off uses the exact desktop GTK palette. On follows wallpaper colors on Android 12+.", 13, ui.palette.muted, false));
        content.addView(appearance);

        content.addView(ui.sectionTitle("Permissions and integrations"));
        addCoreTiles(content, true);
        content.addView(ui.sectionTitle("Optional root bridge"));
        content.addView(rootBridgeTile());
        if (Build.VERSION.SDK_INT >= 36) content.addView(promotedTile());

        content.addView(ui.sectionTitle("Diagnostics"));
        content.addView(ui.quickTile("●", "Test notification", "Send a sample progress notification", ui.palette.green,
                v -> new NotificationController(this).post(HudUpdate.sample())));
        content.addView(ui.quickTile("▣", "Test floating HUD", "Show a sample overlay card", ui.palette.cyan, v -> testOverlay()));

        content.addView(ui.sectionTitle("Command runner"));
        content.addView(ui.text("Advanced raw commands always use the rootless Termux backend. The optional root bridge accepts only visual-builder devtool workflows.", 13, ui.palette.muted, false));
        LinearLayout runner = ui.surface();
        rawCommandInput = ui.input(AppSettings.command(this), "Raw devtool command", true);
        workdirInput = ui.input(AppSettings.workdir(this), "Termux working directory", false);
        backgroundToggle = ui.toggle("Run through Termux in background", AppSettings.runInBackground(this));
        overlayDefaultToggle = ui.toggle("Request floating HUD by default", AppSettings.overlayByDefault(this));
        runner.addView(rawCommandInput);
        runner.addView(ui.secondaryButton("Paste command from clipboard", v -> pasteRawCommandFromClipboard()));
        runner.addView(workdirInput);
        runner.addView(backgroundToggle);
        runner.addView(overlayDefaultToggle);
        runner.addView(ui.button("Save runner settings", ui.palette.green, v -> saveCommandSettings()));
        runner.addView(ui.secondaryButton("Run raw command", v -> runConfiguredCommand()));
        content.addView(runner);

        content.addView(ui.sectionTitle("Secure bridge"));
        LinearLayout bridge = ui.surface();
        tokenView = ui.text(BridgeSecrets.getOrCreateToken(this), 13, ui.palette.text, false);
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
        content.addView(ui.tile("●", "Notifications",
                notifications ? "Live progress and final results can be posted." : "Required for background progress updates.",
                notifications ? "Ready" : "Needs access",
                notifications ? ui.palette.green : ui.palette.yellow,
                notifications ? "Manage" : "Grant access", true,
                v -> requestNotifications()));

        boolean overlayPermission = OverlayHudService.hasPermission(this);
        content.addView(ui.tile("▣", "Display over apps",
                overlayPermission ? "The optional floating HUD can appear above other apps." : "Required only when you use the floating HUD.",
                overlayPermission ? "Ready" : "Optional",
                overlayPermission ? ui.palette.green : ui.palette.cyan,
                overlayPermission ? "Manage" : "Grant access", true,
                v -> openOverlaySettings()));

        boolean termuxInstalled = TermuxCommandRunner.isInstalled(this);
        boolean termuxPermission = TermuxCommandRunner.hasPermission(this);
        String termuxState = !termuxInstalled ? "Missing" : (termuxPermission ? "Ready" : "Needs access");
        String termuxAction = !termuxInstalled ? "Open app info" : (termuxPermission ? "Manage" : "Grant access");
        content.addView(ui.tile("$", "Termux command access",
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
        content.addView(ui.tile("↔", "Termux bridge token",
                BridgeTokenManager.detail(this), bridgeStateLabel(bridgeState),
                bridgeReady ? ui.palette.green : (bridgeState.equals("outdated") ? ui.palette.yellow : ui.palette.cyan),
                BridgeTokenManager.actionLabel(this), !bridgeWorking && !bridgeState.equals("termux-missing"),
                v -> bridgeTokenAction()));

        if (includeFloatingHud) {
            boolean running = OverlayHudService.isRunning();
            boolean enabled = OverlayHudService.isEnabled(this);
            String state = running ? "Running" : (enabled ? "Enabled" : "Off");
            String action = running || enabled ? "Disable" : "Enable";
            content.addView(ui.tile("▤", "Floating HUD",
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
        return ui.tile("#", "Devtool Root Bridge", RootBridgeManager.detail(this),
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
        return ui.tile("▲", "Promoted live updates",
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
        LinearLayout heading = new LinearLayout(this);
        heading.setOrientation(LinearLayout.HORIZONTAL);
        heading.setGravity(Gravity.CENTER_VERTICAL);
        TextView title = ui.text(record.title, 17, ui.palette.text, true);
        heading.addView(title, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        TextView badge = ui.text(record.status.toUpperCase(), 11, statusColor(record.status), true);
        badge.setPadding(ui.dp(9), ui.dp(5), ui.dp(9), ui.dp(5));
        badge.setBackground(ui.flat(ui.palette.surfaceRaised, 999));
        heading.addView(badge);
        card.addView(heading);

        String context = join(" · ", record.phase, record.repo, record.target);
        if (!context.isEmpty()) {
            TextView contextView = ui.text(context, 13, ui.palette.muted, false);
            contextView.setPadding(0, ui.dp(5), 0, 0);
            card.addView(contextView);
        }
        if (!record.detail.isEmpty()) {
            TextView detail = ui.text(record.detail, 14, ui.palette.text, false);
            detail.setPadding(0, ui.dp(8), 0, 0);
            card.addView(detail);
        }
        LinearLayout pipeline = new LinearLayout(this);
        pipeline.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams pipelineParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        pipelineParams.setMargins(0, ui.dp(12), 0, ui.dp(4));
        pipeline.setLayoutParams(pipelineParams);
        java.util.List<HudUpdate.Step> steps = HudUpdate.parseSteps(
                record.stepsJson, record.phase, record.status, record.progress);
        LinearLayout row = null;
        int rowCount = 0;
        for (HudUpdate.Step step : steps) {
            if (row == null || rowCount == 4) {
                row = new LinearLayout(this);
                row.setOrientation(LinearLayout.HORIZONTAL);
                pipeline.addView(row, new LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
                rowCount = 0;
            }
            LinearLayout segment = new LinearLayout(this);
            segment.setOrientation(LinearLayout.VERTICAL);
            segment.setPadding(ui.dp(2), 0, ui.dp(2), ui.dp(3));
            TextView stepLabel = ui.text(step.label.toUpperCase(java.util.Locale.ROOT), 9, stepColor(step), true);
            stepLabel.setGravity(Gravity.CENTER);
            stepLabel.setMaxLines(1);
            stepLabel.setEllipsize(android.text.TextUtils.TruncateAt.END);
            segment.addView(stepLabel, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
            ProgressBar stepProgress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
            stepProgress.setMax(100);
            stepProgress.setIndeterminate(step.running() && step.progress <= 0);
            stepProgress.setProgress((step.succeeded() || step.failed() || step.skipped()) ? 100 : step.progress);
            stepProgress.setProgressTintList(android.content.res.ColorStateList.valueOf(stepColor(step)));
            stepProgress.setProgressBackgroundTintList(android.content.res.ColorStateList.valueOf(ui.palette.surfaceRaised));
            segment.addView(stepProgress, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, ui.dp(6)));
            row.addView(segment, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            rowCount++;
        }
        if (row != null) {
            while (rowCount < 4) {
                android.view.View spacer = new android.view.View(this);
                row.addView(spacer, new LinearLayout.LayoutParams(0, 1, 1f));
                rowCount++;
            }
        }
        card.addView(pipeline);

        String time = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
                .format(new Date(record.updatedAtMs));
        card.addView(ui.text(record.errors + " errors · " + record.warnings + " warnings · " + time,
                12, ui.palette.muted, false));

        if (actions) {
            if (!record.command.isEmpty()) {
                card.addView(ui.secondaryButton("Run again", v -> runRaw(record.command, record.workdir, AppSettings.overlayByDefault(this), AppSettings.runConfig(this).useRootBridge)));
                card.addView(ui.secondaryButton("Copy command", v -> copyText("Devtool command", record.command)));
            }
            if (!record.terminal() && record.runId.startsWith("root-")) {
                card.addView(ui.secondaryButton("Cancel root run", v -> {
                    RootBridgeManager.cancel(this, record.runId);
                    Toast.makeText(this, "Cancellation requested", Toast.LENGTH_SHORT).show();
                }));
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

    private void runBuiltCommand() {
        try {
            DevtoolRunConfig config = readRunConfig();
            String command = DevtoolCommandBuilder.build(config);
            AppSettings.setRunConfig(this, config);
            AppSettings.setCommand(this, command);
            String workdir = resolvedWorkdir(config);
            AppSettings.setWorkdir(this, workdir);
            runRaw(command, workdir, config.overlay, config.useRootBridge);
        } catch (IllegalArgumentException error) {
            Toast.makeText(this, error.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private String resolvedWorkdir(DevtoolRunConfig config) {
        String configured = config.workdir == null ? "" : config.workdir.trim();
        if (config.useChroot) {
            // Termux RUN_COMMAND validates its work directory before the shell
            // starts, so a chroot-only path such as /home/mike/Code/project is
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
        TermuxCommandRunner.LaunchResult result = useRootBridge
                ? RootBridgeManager.run(this, command, workdir, overlay)
                : TermuxCommandRunner.run(this, command, workdir, AppSettings.runInBackground(this), overlay, null);
        Toast.makeText(this,
                result.started ? "Started · run " + result.executionId : result.error,
                result.started ? Toast.LENGTH_SHORT : Toast.LENGTH_LONG).show();
        if (result.started) switchPage(0);
    }

    private DevtoolRunConfig readRunConfig() {
        DevtoolRunConfig config = new DevtoolRunConfig();
        config.operation = String.valueOf(operationInput.getSelectedItem());
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
        config.useChroot = chrootToggle.isChecked();
        config.useRootBridge = rootBridgeToggle != null && rootBridgeToggle.isChecked();
        config.copy = copyToggle.isChecked();
        config.copyViaTermux = copyViaTermuxToggle.isChecked();
        config.autoHud = autoHudToggle.isChecked();
        config.validate = validateToggle.isChecked();
        config.commit = commitToggle.isChecked();
        config.assumeYes = yesToggle.isChecked();
        config.overlay = runOverlayToggle.isChecked();
        return config;
    }

    private void saveRunConfig() {
        AppSettings.setRunConfig(this, readRunConfig());
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
        chrootToggle.setChecked(config.useChroot);
        if (rootBridgeToggle != null) rootBridgeToggle.setChecked(config.useRootBridge && RootBridgeManager.isReady(this));
        copyToggle.setChecked(config.copy);
        copyViaTermuxToggle.setChecked(config.copyViaTermux);
        autoHudToggle.setChecked(config.autoHud);
        validateToggle.setChecked(config.validate);
        commitToggle.setChecked(config.commit);
        yesToggle.setChecked(config.assumeYes);
        runOverlayToggle.setChecked(config.overlay);
        AppSettings.setRunConfig(this, config);
        updatePreview();
    }

    private void pasteRawCommandFromClipboard() {
        String command = clipboardText();
        if (command == null) return;
        rawCommandInput.setText(command);
        Toast.makeText(this, "Command pasted", Toast.LENGTH_SHORT).show();
    }

    private String clipboardText() {
        ClipboardManager clipboard = getSystemService(ClipboardManager.class);
        if (clipboard == null || !clipboard.hasPrimaryClip() || clipboard.getPrimaryClip() == null
                || clipboard.getPrimaryClip().getItemCount() == 0) {
            Toast.makeText(this, "Clipboard is empty or unavailable", Toast.LENGTH_LONG).show();
            return null;
        }
        CharSequence text = clipboard.getPrimaryClip().getItemAt(0).coerceToText(this);
        String command = text == null ? "" : text.toString().trim();
        if (command.isEmpty()) {
            Toast.makeText(this, "Clipboard does not contain text", Toast.LENGTH_LONG).show();
            return null;
        }
        return command;
    }

    private void updatePreview() {
        if (commandPreview == null || operationInput == null) return;
        try {
            commandPreview.setText(DevtoolCommandBuilder.build(readRunConfig()));
            commandPreview.setTextColor(ui.palette.text);
        } catch (IllegalArgumentException error) {
            commandPreview.setText(error.getMessage());
            commandPreview.setTextColor(ui.palette.red);
        }
    }

    private EditText field(LinearLayout parent, String label, String value, String hint, boolean multiline) {
        parent.addView(label(label));
        EditText input = ui.input(value, hint, multiline);
        parent.addView(input);
        return input;
    }

    private TextView label(String value) {
        TextView label = ui.text(value, 12, ui.palette.muted, true);
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
        AppSettings.setRunInBackground(this, backgroundToggle.isChecked());
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
        statusView.setText(
                "Notifications  " + yesNo(notifications) +
                "\nPromoted updates  " + promoted +
                "\nOverlay permission  " + yesNo(OverlayHudService.hasPermission(this)) +
                "\nFloating HUD  " + (OverlayHudService.isRunning() ? "running" :
                        (OverlayHudService.isEnabled(this) ? "enabled" : "disabled")) +
                "\nTermux installed  " + yesNo(TermuxCommandRunner.isInstalled(this)) +
                "\nTermux command permission  " + yesNo(TermuxCommandRunner.hasPermission(this)) +
                "\nOptional root bridge  " + RootBridgeManager.stateLabel(this));
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
        if (status.equals("failed") || status.equals("cancelled") || status.equals("interrupted")) return ui.palette.red;
        if (status.equals("waiting")) return ui.palette.yellow;
        return ui.palette.cyan;
    }

    private void switchPage(int page) {
        currentPage = page;
        AppSettings.setPage(this, page);
        rebuildUi();
    }

    private static int indexOf(String[] values, String wanted) {
        for (int i = 0; i < values.length; i++) if (values[i].equals(wanted)) return i;
        return 0;
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
        if (Build.VERSION.SDK_INT >= 23 && !TermuxCommandRunner.hasPermission(this)) {
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

    private void copyToken() {
        copyText("Devtool Live HUD token", BridgeSecrets.getOrCreateToken(this));
    }

    private void copyText(String label, String value) {
        ClipboardManager clipboard = getSystemService(ClipboardManager.class);
        clipboard.setPrimaryClip(ClipData.newPlainText(label, value));
        Toast.makeText(this, "Copied", Toast.LENGTH_SHORT).show();
    }

    private static final class SimpleItemSelectedListener implements android.widget.AdapterView.OnItemSelectedListener {
        private final Runnable action;
        SimpleItemSelectedListener(Runnable action) { this.action = action; }
        @Override public void onItemSelected(android.widget.AdapterView<?> parent, View view, int position, long id) { action.run(); }
        @Override public void onNothingSelected(android.widget.AdapterView<?> parent) { action.run(); }
    }
}
