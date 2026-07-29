package io.github.mikeceead.devtoolhud;

import android.app.NotificationManager;
import android.content.Context;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Read-only health summary for the Debug Workbench cockpit.
 *
 * The workbench intentionally composes existing recorder/probe/doctor surfaces instead
 * of running mutable Git, validation or repository actions while rendering. The only
 * active operation it can launch is the existing bridge roundtrip probe from the UI.
 */
final class DebugWorkbenchHealth {
    static final String BUNDLE_KEY = "debug_workbench";
    private static final int MAX_RECENT_EVENTS = 80;
    private static final int MAX_REPOSITORIES = 18;

    private DebugWorkbenchHealth() {}

    static Snapshot assess(Context context) {
        Context app = context.getApplicationContext();
        Snapshot snapshot = new Snapshot();
        snapshot.generatedAtMs = System.currentTimeMillis();
        snapshot.schemaVersion = DevtoolDatabase.schemaVersion();

        NotificationManager manager = app.getSystemService(NotificationManager.class);
        add(snapshot, component("Notifications", manager != null && manager.areNotificationsEnabled() ? "pass" : "warn",
                manager != null && manager.areNotificationsEnabled() ? "Ready" : "Needs access",
                manager != null && manager.areNotificationsEnabled()
                        ? "Background progress notifications can post."
                        : "Grant notification permission before relying on long-running progress."));
        add(snapshot, component("Floating HUD", OverlayHudService.hasPermission(app) ? "pass" : "warn",
                OverlayHudService.hasPermission(app) ? "Ready" : "Optional",
                OverlayHudService.hasPermission(app)
                        ? (OverlayHudService.isRunning() ? "Overlay service is running." : "Overlay permission is granted.")
                        : "Display-over-apps is only needed when using the floating HUD."));
        add(snapshot, component("Termux", TermuxCommandRunner.isInstalled(app) ? "pass" : "fail",
                TermuxCommandRunner.isInstalled(app) ? "Installed" : "Missing",
                TermuxCommandRunner.isInstalled(app)
                        ? "Termux package is visible to the app."
                        : "Install Termux or make it visible to this app."));
        add(snapshot, component("Termux RunCommand", TermuxCommandRunner.hasPermission(app) ? "pass" : "fail",
                TermuxCommandRunner.hasPermission(app) ? "Ready" : "Needs access",
                TermuxCommandRunner.hasPermission(app)
                        ? "The app can request command execution through Termux."
                        : "Grant com.termux.permission.RUN_COMMAND before launching devtool."));
        add(snapshot, component("Bridge token", BridgeTokenManager.isReady(app) ? "pass" : bridgeTokenStatus(app),
                bridgeStateLabel(BridgeTokenManager.state(app)), BridgeTokenManager.detail(app)));
        add(snapshot, component("Bridge roundtrip", bridgeRoundtripStatus(app), BridgeRoundtripProbe.label(app), BridgeRoundtripProbe.detail(app)));
        add(snapshot, component("Root bridge", rootBridgeStatus(app), RootBridgeManager.stateLabel(app), RootBridgeManager.detail(app)));
        add(snapshot, component("Repository refresh", RepositoryStore.busy() ? "warn" : "pass",
                RepositoryStore.busy() ? "Refreshing" : "Idle",
                RepositoryStore.busy() ? "Repository scanner is currently busy." : "Repository scanner is idle."));
        add(snapshot, component("Log indexer", RootLogIndexer.busy() ? "warn" : "pass",
                RootLogIndexer.busy() ? "Indexing" : "Idle",
                RootLogIndexer.busy() ? "Root log indexer is refreshing metadata." : "Root log indexer is idle."));

        snapshot.latestRun = RunStore.latest(app);
        snapshot.activeRun = RunStore.active(app);
        snapshot.latestValidation = ValidationStore.latest(app);
        snapshot.activeValidation = ValidationStore.active(app);
        snapshot.recentEvents = DevtoolDatabase.get(app).recentDebugEvents(MAX_RECENT_EVENTS);
        for (DebugEventRecord event : snapshot.recentEvents) {
            if (event.problem()) snapshot.problemDebugEvents++;
        }
        snapshot.repositories = RepositoryStore.all(app);
        for (RepositoryRecord repository : snapshot.repositories) {
            RepositoryPathResolution path = RepositoryPathResolver.resolve(repository);
            RepoQueueRecovery.QueueHealth queue = RepoQueueRecovery.assess(app, repository);
            if (path.status.equals(RepositoryPathResolution.STATUS_FAIL) || queue.status.equals("fail")) snapshot.repoFailures++;
            else if (path.status.equals(RepositoryPathResolution.STATUS_WARN) || queue.status.equals("warn")) snapshot.repoWarnings++;
        }
        if (snapshot.problemDebugEvents > 0) add(snapshot, component("Recent debug events", "warn",
                snapshot.problemDebugEvents + " problem" + plural(snapshot.problemDebugEvents),
                "Recent recorder events include warnings or errors."));
        else add(snapshot, component("Recent debug events", "pass", "Quiet", "No recent warning/error debug events in the retained sample."));
        if (snapshot.repoFailures > 0) add(snapshot, component("Repo diagnostics", "fail",
                snapshot.repoFailures + " failing", "Repository doctor/path/queue checks found failures."));
        else if (snapshot.repoWarnings > 0) add(snapshot, component("Repo diagnostics", "warn",
                snapshot.repoWarnings + " warning" + plural(snapshot.repoWarnings), "Repository doctor/path/queue checks found warnings."));
        else add(snapshot, component("Repo diagnostics", "pass", "Clear", "Saved repository metadata and queues do not show obvious warnings."));

        return snapshot;
    }

    static JSONObject supportSummary(Context context) throws JSONException {
        Snapshot snapshot = assess(context);
        JSONObject value = new JSONObject();
        value.put("status", snapshot.status());
        value.put("summary", snapshot.summary());
        value.put("generated_at_ms", snapshot.generatedAtMs);
        value.put("schema_version", snapshot.schemaVersion);
        value.put("component_total", snapshot.components.size());
        value.put("component_failures", snapshot.failures);
        value.put("component_warnings", snapshot.warnings);
        value.put("recent_problem_debug_events", snapshot.problemDebugEvents);
        value.put("repository_failures", snapshot.repoFailures);
        value.put("repository_warnings", snapshot.repoWarnings);
        value.put("active_run_id", snapshot.activeRun == null ? "" : snapshot.activeRun.runId);
        value.put("latest_run_status", snapshot.latestRun == null ? "none" : snapshot.latestRun.status);
        value.put("active_validation_session_id", snapshot.activeValidation == null ? "" : snapshot.activeValidation.sessionId);
        value.put("latest_validation_state", snapshot.latestValidation == null ? "none" : snapshot.latestValidation.effectiveState);
        JSONArray components = new JSONArray();
        for (Component component : snapshot.components) {
            JSONObject item = new JSONObject();
            item.put("name", component.name);
            item.put("status", component.status);
            item.put("label", component.label);
            item.put("detail", component.detail);
            components.put(item);
        }
        value.put("components", components);
        JSONArray events = new JSONArray();
        int count = 0;
        for (DebugEventRecord event : snapshot.recentEvents) {
            if (!event.problem() && count >= 12) continue;
            JSONObject item = new JSONObject();
            item.put("timestamp_ms", event.timestampMs);
            item.put("severity", event.severity);
            item.put("category", event.category);
            item.put("event_type", event.eventType);
            item.put("run_id", event.runId);
            item.put("repository_id", event.repositoryId);
            item.put("operation_id", event.operationId);
            item.put("message", event.message);
            events.put(item);
            count++;
            if (count >= 24) break;
        }
        value.put("recent_events", events);
        return value;
    }

    static String plainText(Context context) {
        Snapshot snapshot = assess(context);
        StringBuilder text = new StringBuilder();
        text.append("Debug Workbench health\n");
        text.append("status=").append(snapshot.status()).append('\n');
        text.append("summary=").append(snapshot.summary()).append('\n');
        text.append("components\n");
        for (Component component : snapshot.components) {
            text.append("- ").append(component.name).append(": ").append(component.status)
                    .append(" · ").append(component.label).append(" · ").append(component.detail).append('\n');
        }
        if (snapshot.latestRun != null) text.append("latest_run=").append(snapshot.latestRun.runId)
                .append(" ").append(snapshot.latestRun.status).append(" ").append(snapshot.latestRun.phase).append('\n');
        if (snapshot.latestValidation != null) text.append("latest_validation=").append(snapshot.latestValidation.sessionId)
                .append(" ").append(snapshot.latestValidation.effectiveState).append('\n');
        text.append("recent_debug_events\n");
        int count = 0;
        for (DebugEventRecord event : snapshot.recentEvents) {
            text.append("- ").append(event.severity).append("/").append(event.category)
                    .append(" ").append(event.eventType).append(": ").append(event.message).append('\n');
            if (++count >= 24) break;
        }
        return text.toString();
    }

    private static void add(Snapshot snapshot, Component component) {
        snapshot.components.add(component);
        if (component.status.equals("fail")) snapshot.failures++;
        else if (component.status.equals("warn")) snapshot.warnings++;
        else snapshot.passes++;
    }

    private static Component component(String name, String status, String label, String detail) {
        Component component = new Component();
        component.name = name;
        component.status = clean(status).equals("fail") ? "fail" : clean(status).equals("warn") ? "warn" : "pass";
        component.label = clean(label);
        component.detail = clean(detail);
        return component;
    }

    private static String bridgeTokenStatus(Context context) {
        String state = BridgeTokenManager.state(context);
        if (state.equals("connected")) return "pass";
        if (state.equals("permission-needed") || state.equals("termux-missing") || state.equals("outdated")) return "fail";
        return "warn";
    }

    private static String bridgeRoundtripStatus(Context context) {
        String state = BridgeRoundtripProbe.state(context);
        if (state.equals("passed")) return "pass";
        if (state.equals("failed") || state.equals("permission-needed") || state.equals("termux-missing")) return "fail";
        return "warn";
    }

    private static String rootBridgeStatus(Context context) {
        String state = RootBridgeManager.state(context);
        if (state.equals("ready")) return "pass";
        if (state.equals("denied") || state.equals("error")) return "fail";
        return "warn";
    }

    private static String bridgeStateLabel(String state) {
        if (state.equals("connected")) return "Connected";
        if (state.equals("outdated")) return "Update needed";
        if (state.equals("working")) return "Checking";
        if (state.equals("permission-needed")) return "Needs access";
        if (state.equals("termux-missing")) return "Unavailable";
        return "Not installed";
    }

    private static String plural(int value) { return value == 1 ? "" : "s"; }

    private static String clean(String value) { return value == null ? "" : value.trim(); }

    static final class Snapshot {
        long generatedAtMs;
        int schemaVersion;
        int passes;
        int warnings;
        int failures;
        int problemDebugEvents;
        int repoWarnings;
        int repoFailures;
        RunRecord latestRun;
        RunRecord activeRun;
        ValidationSessionRecord latestValidation;
        ValidationSessionRecord activeValidation;
        final List<Component> components = new ArrayList<>();
        List<DebugEventRecord> recentEvents = new ArrayList<>();
        List<RepositoryRecord> repositories = new ArrayList<>();

        String status() {
            if (failures > 0) return "fail";
            if (warnings > 0) return "warn";
            return "pass";
        }

        String summary() {
            if (failures > 0) return failures + " failing · " + warnings + " warning" + plural(warnings);
            if (warnings > 0) return warnings + " warning" + plural(warnings) + " · " + passes + " clear";
            return passes + " checks clear";
        }
    }

    static final class Component {
        String name = "";
        String status = "pass";
        String label = "";
        String detail = "";
    }
}
