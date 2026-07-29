package io.github.mikeceead.devtoolhud;

import android.app.NotificationManager;
import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.text.DateFormat;
import java.util.Date;
import java.util.List;
import java.util.Locale;

final class SupportBundle {
    private static final int MAX_RUNS = 12;
    private static final int MAX_REPOSITORIES = 24;
    private static final int MAX_OPERATIONS_PER_REPOSITORY = 6;
    private static final int MAX_TEXT = 2_000;
    private static final int MAX_DEBUG_EVENTS = 160;
    private static final int MAX_TRACE_EVENTS = 96;
    private static final int MAX_BUNDLE_REPOSITORIES = 16;
    private static final int MAX_RECENT_GIT_OPERATIONS = 24;

    private SupportBundle() {}

    static String build(Context context) {
        Context app = context.getApplicationContext();
        JSONObject root = new JSONObject();
        String token = BridgeSecrets.getOrCreateToken(app);
        try {
            root.put("schema", 2);
            root.put("bundle_version", "support_bundle_v2");
            root.put("kind", "devtool_android_support_bundle");
            root.put("generated_at", DateFormat.getDateTimeInstance().format(new Date()));
            root.put("privacy", privacy(app));
            root.put("app", app(app));
            root.put("device", device());
            root.put("summary", summary(app));
            root.put("root_bridge", rootBridge(app, token));
            root.put("bridge_roundtrip", bridgeRoundtrip(app));
            root.put("debug_workbench", DebugWorkbenchHealth.supportSummary(app));
            root.put("guided_fixes", DebugGuidedFix.supportSummary(app));
            root.put("latest_probe_results", latestProbeResults(app));
            root.put("debug_events", debugEvents(app, token));
            root.put("run_command_trace", runCommandTrace(app, token));
            root.put("repo_doctor_results", repoDoctorResults(app));
            root.put("repo_execution_plan", repoExecutionPlan(app));
            root.put("repo_path_resolution", repoPathResolution(app));
            root.put("repo_queue_health", repoQueueHealth(app));
            root.put("recent_git_operations", recentGitOperations(app, token));
            root.put("validation_health", validationHealth(app));
            root.put("notification_health", notificationHealth(app));
            root.put("redaction_self_test", redactionSelfTest(token));
            root.put("settings", settings(app));
            root.put("database", database(app));
            root.put("active_run", run(RunStore.active(app), token, AppSettings.includeSupportCommands(app)));
            root.put("recent_runs", recentRuns(app, token));
            root.put("repositories", repositories(app, token));
            root.put("notes", "Support Bundle v2 exports debug cockpit evidence as bounded redacted JSON sections. Commands are omitted unless enabled in Settings. Included text is redacted for bridge tokens, HTTPS credentials, authorization headers and common secret assignments.");
        } catch (JSONException ignored) {
            // JSONObject only throws for unsupported value types, which are not used here.
        }
        try {
            return root.toString(2);
        } catch (JSONException error) {
            return root.toString();
        }
    }

    private static JSONObject privacy(Context context) throws JSONException {
        JSONObject value = new JSONObject();
        value.put("commands_included", AppSettings.includeSupportCommands(context));
        value.put("raw_log_output_included", false);
        value.put("bridge_token_included", false);
        value.put("credential_values_included", false);
        value.put("redaction", "bridge token, HTTPS credentials, Authorization headers, token/password/secret/key assignments and private-key blocks");
        return value;
    }

    private static JSONObject app(Context context) throws JSONException {
        JSONObject value = new JSONObject();
        value.put("package", context.getPackageName());
        value.put("version_name", packageVersionName(context));
        value.put("version_code", packageVersionCode(context));
        value.put("build_config_version_name", BuildConfig.VERSION_NAME);
        value.put("build_config_version_code", BuildConfig.VERSION_CODE);
        return value;
    }

    private static JSONObject device() throws JSONException {
        JSONObject value = new JSONObject();
        value.put("sdk", Build.VERSION.SDK_INT);
        value.put("release", Build.VERSION.RELEASE == null ? "" : Build.VERSION.RELEASE);
        value.put("manufacturer", Build.MANUFACTURER == null ? "" : Build.MANUFACTURER);
        value.put("model", Build.MODEL == null ? "" : Build.MODEL);
        return value;
    }

    private static JSONObject rootBridge(Context context, String token) throws JSONException {
        JSONObject value = new JSONObject();
        value.put("state", RootBridgeManager.state(context));
        value.put("label", RootBridgeManager.stateLabel(context));
        value.put("version", RootBridgeManager.version(context));
        value.put("ready", RootBridgeManager.isReady(context));
        value.put("detail", redact(RootBridgeManager.detail(context), token));
        return value;
    }

    private static JSONObject bridgeRoundtrip(Context context) throws JSONException {
        JSONObject value = new JSONObject(BridgeRoundtripProbe.supportSummary(context));
        return value;
    }

    private static JSONObject settings(Context context) throws JSONException {
        JSONObject value = new JSONObject();
        DevtoolRunConfig config = AppSettings.runConfig(context);
        value.put("monet", AppSettings.useMonet(context));
        value.put("run_in_background", AppSettings.runInBackground(context));
        value.put("overlay_by_default", AppSettings.overlayByDefault(context));
        value.put("current_page", AppSettings.page(context));
        value.put("default_operation", config.operation);
        value.put("default_project_type", config.projectType);
        value.put("default_execution_environment", config.executionEnvironment);
        value.put("default_use_chroot", config.useChroot);
        value.put("default_chroot", config.chrootName);
        value.put("default_use_root_bridge", config.useRootBridge);
        value.put("retention_run_days", AppSettings.completedRunRetentionDays(context));
        value.put("retention_max_runs", AppSettings.maximumCompletedRuns(context));
        value.put("retention_operation_days", AppSettings.repositoryOperationRetentionDays(context));
        value.put("retention_event_days", AppSettings.repositoryEventRetentionDays(context));
        value.put("support_commands_enabled", AppSettings.includeSupportCommands(context));
        return value;
    }

    private static JSONObject database(Context context) throws JSONException {
        DevtoolDatabase.DatabaseStats stats = DevtoolDatabase.get(context).databaseStats();
        JSONObject value = new JSONObject();
        value.put("schema_version", DevtoolDatabase.schemaVersion());
        value.put("runs", stats.runs);
        value.put("pinned_runs", stats.pinnedRuns);
        value.put("log_entries", stats.logEntries);
        value.put("repositories", stats.repositories);
        value.put("git_operations", stats.operations);
        value.put("debug_events", stats.debugEvents);
        value.put("database_bytes", stats.databaseBytes);
        value.put("root_log_indexer_busy", RootLogIndexer.busy());
        value.put("repository_refresh_busy", RepositoryStore.busy());
        return value;
    }

    private static JSONArray recentRuns(Context context, String token) throws JSONException {
        JSONArray values = new JSONArray();
        boolean includeCommands = AppSettings.includeSupportCommands(context);
        List<RunRecord> runs = RunStore.all(context);
        int limit = Math.min(MAX_RUNS, runs.size());
        for (int i = 0; i < limit; i++) values.put(run(runs.get(i), token, includeCommands));
        return values;
    }

    private static JSONObject run(RunRecord run, String token, boolean includeCommand) throws JSONException {
        JSONObject value = new JSONObject();
        if (run == null) {
            value.put("present", false);
            return value;
        }
        value.put("present", true);
        value.put("run_id", run.runId);
        value.put("status", run.status);
        value.put("phase", run.phase);
        value.put("project_type", run.projectType);
        value.put("execution_environment", run.executionEnvironment);
        value.put("repository_id", run.repositoryId);
        value.put("repo", redact(run.repo, token));
        value.put("workdir", redact(run.workdir, token));
        value.put("target", run.target);
        value.put("progress", run.progress);
        value.put("current", run.current);
        value.put("total", run.total);
        value.put("warnings", run.warnings);
        value.put("errors", run.errors);
        value.put("exit_code", run.exitCode);
        value.put("pinned", run.pinned);
        value.put("updated_at_ms", run.updatedAtMs);
        value.put("duration_ms", run.durationMs);
        value.put("artifact", redact(run.artifact, token));
        value.put("commit_hash", run.commitHash);
        value.put("transaction_backup", run.transactionBackup);
        value.put("log_source_id", run.logSourceId);
        value.put("log_path", redact(run.logPath, token));
        value.put("summary", truncate(redact(run.summary, token)));
        value.put("detail", truncate(redact(run.detail, token)));
        value.put("command", includeCommand ? truncate(redact(run.command, token)) : "<disabled>");
        return value;
    }

    private static JSONArray repositories(Context context, String token) throws JSONException {
        JSONArray values = new JSONArray();
        List<RepositoryRecord> repositories = RepositoryStore.all(context);
        int limit = Math.min(MAX_REPOSITORIES, repositories.size());
        for (int i = 0; i < limit; i++) {
            RepositoryRecord repository = repositories.get(i);
            JSONObject value = new JSONObject();
            value.put("repository_id", repository.repositoryId);
            value.put("name", repository.name());
            value.put("environment", repository.environment);
            value.put("chroot", repository.chrootName);
            value.put("linux_user", repository.linuxUser);
            value.put("canonical_path", redact(repository.canonicalPath, token));
            value.put("host_path", redact(repository.hostPath, token));
            value.put("branch", repository.branch);
            value.put("upstream", repository.upstream);
            value.put("remote", redact(repository.remote, token));
            value.put("status", repository.status);
            value.put("dirty", repository.dirty);
            value.put("ahead", repository.aheadCount);
            value.put("behind", repository.behindCount);
            value.put("active_runs", repository.activeRunCount);
            value.put("saved", repository.saved);
            value.put("project_type", repository.projectType);
            value.put("last_error", truncate(redact(repository.lastError, token)));
            value.put("repository_doctor", RepositoryDoctor.supportSummary(context, repository));
            value.put("path_resolution", RepositoryPathResolver.supportSummary(repository));
            value.put("repo_queue_health", RepoQueueRecovery.supportSummary(context, repository));
            value.put("operations", operations(context, repository.repositoryId, token));
            values.put(value);
        }
        return values;
    }

    private static JSONArray operations(Context context, String repositoryId, String token) throws JSONException {
        JSONArray values = new JSONArray();
        List<RepositoryOperationRecord> operations = DevtoolDatabase.get(context)
                .repositoryOperations(repositoryId, MAX_OPERATIONS_PER_REPOSITORY);
        for (RepositoryOperationRecord operation : operations) {
            JSONObject value = new JSONObject();
            value.put("operation_id", operation.operationId);
            value.put("run_id", operation.runId);
            value.put("type", operation.type);
            value.put("target", redact(operation.target, token));
            value.put("state", operation.state);
            value.put("progress_label", operation.progressLabel);
            value.put("summary", truncate(redact(operation.summary, token)));
            value.put("queued_at", operation.queuedAt);
            value.put("started_at", operation.startedAt);
            value.put("finished_at", operation.finishedAt);
            value.put("exit_code", operation.exitCode);
            value.put("cancellable", operation.cancellable);
            value.put("git_operation_trace", GitOperationTrace.supportSummary(context, operation, token));
            values.put(value);
        }
        return values;
    }


    private static JSONObject summary(Context context) throws JSONException {
        DebugWorkbenchHealth.Snapshot snapshot = DebugWorkbenchHealth.assess(context);
        JSONObject value = new JSONObject();
        value.put("status", snapshot.status());
        value.put("summary", snapshot.summary());
        value.put("generated_at_ms", snapshot.generatedAtMs);
        value.put("schema_version", snapshot.schemaVersion);
        value.put("active_run_id", snapshot.activeRun == null ? "" : snapshot.activeRun.runId);
        value.put("latest_run_id", snapshot.latestRun == null ? "" : snapshot.latestRun.runId);
        value.put("latest_run_status", snapshot.latestRun == null ? "none" : snapshot.latestRun.status);
        value.put("active_validation_session_id", snapshot.activeValidation == null ? "" : snapshot.activeValidation.sessionId);
        value.put("latest_validation_state", snapshot.latestValidation == null ? "none" : snapshot.latestValidation.effectiveState);
        value.put("recent_problem_debug_events", snapshot.problemDebugEvents);
        value.put("repository_failures", snapshot.repoFailures);
        value.put("repository_warnings", snapshot.repoWarnings);
        value.put("artifact", "single_json_support_bundle_v2");
        value.put("contains", "summary, debug_events, run_command_trace, latest_probe_results, bridge_roundtrip, repo_doctor_results, repo_execution_plan, repo_path_resolution, repo_queue_health, recent_git_operations, guided_fixes, validation_health, notification_health, redaction_self_test");
        return value;
    }

    private static JSONObject latestProbeResults(Context context) throws JSONException {
        JSONObject value = new JSONObject();
        value.put("bridge_roundtrip", bridgeRoundtrip(context));
        value.put("debug_workbench", DebugWorkbenchHealth.supportSummary(context));
        value.put("guided_fixes", DebugGuidedFix.supportSummary(context));
        value.put("root_bridge", rootBridge(context, BridgeSecrets.getOrCreateToken(context)));
        value.put("termux", probe("Termux", TermuxCommandRunner.isInstalled(context) ? "pass" : "fail",
                TermuxCommandRunner.isInstalled(context) ? "Installed" : "Missing"));
        value.put("termux_run_command", probe("Termux RunCommand",
                TermuxCommandRunner.hasPermission(context) ? "pass" : "fail",
                TermuxCommandRunner.hasPermission(context) ? "Ready" : "Permission missing"));
        value.put("bridge_token", probe("Bridge token", BridgeTokenManager.isReady(context) ? "pass" : "warn",
                BridgeTokenManager.detail(context)));
        value.put("repository_refresh", probe("Repository refresh", RepositoryStore.busy() ? "warn" : "pass",
                RepositoryStore.busy() ? "Refreshing" : "Idle"));
        value.put("log_indexer", probe("Log indexer", RootLogIndexer.busy() ? "warn" : "pass",
                RootLogIndexer.busy() ? "Indexing" : "Idle"));
        return value;
    }

    private static JSONObject probe(String name, String status, String detail) throws JSONException {
        JSONObject value = new JSONObject();
        value.put("name", name);
        value.put("status", status);
        value.put("detail", detail);
        return value;
    }

    private static JSONArray debugEvents(Context context, String token) throws JSONException {
        JSONArray values = new JSONArray();
        for (DebugEventRecord event : DevtoolDatabase.get(context).recentDebugEvents(MAX_DEBUG_EVENTS)) {
            values.put(debugEvent(event, token, true));
        }
        return values;
    }

    private static JSONObject debugEvent(DebugEventRecord event, String token, boolean includeDetails) throws JSONException {
        JSONObject value = new JSONObject();
        value.put("event_id", event.eventId);
        value.put("timestamp_ms", event.timestampMs);
        value.put("severity", event.severity);
        value.put("category", event.category);
        value.put("event_type", event.eventType);
        value.put("surface", event.surface);
        value.put("run_id", event.runId);
        value.put("repository_id", event.repositoryId);
        value.put("operation_id", event.operationId);
        value.put("validation_session_id", event.validationSessionId);
        value.put("fingerprint", event.fingerprint);
        value.put("redaction_state", event.redactionState);
        value.put("message", truncate(redact(event.message, token)));
        if (includeDetails) value.put("detail_json", truncate(redact(event.detailJson, token)));
        return value;
    }

    private static JSONObject runCommandTrace(Context context, String token) throws JSONException {
        RunRecord run = RunStore.active(context);
        if (run == null) run = RunStore.latest(context);
        JSONObject value = new JSONObject();
        if (run == null) {
            value.put("present", false);
            value.put("events", new JSONArray());
            return value;
        }
        value.put("present", true);
        value.put("run", run(run, token, AppSettings.includeSupportCommands(context)));
        JSONArray events = new JSONArray();
        for (DebugEventRecord event : DevtoolDatabase.get(context).debugEventsForRun(run.runId, MAX_TRACE_EVENTS)) {
            String type = RunCommandTrace.normalizeCheckpoint(event.eventType);
            if (event.category.equals("run_trace") || type.startsWith("TERMUX_") || type.startsWith("RUN_")
                    || type.startsWith("COMMAND_") || type.startsWith("BRIDGE_ROUNDTRIP")
                    || type.startsWith("ROOT_BRIDGE")) {
                JSONObject item = debugEvent(event, token, true);
                item.put("checkpoint_order", RunCommandTrace.order(event.eventType));
                events.put(item);
            }
        }
        value.put("events", events);
        value.put("max_events", MAX_TRACE_EVENTS);
        value.put("raw_log_output_included", false);
        return value;
    }

    private static JSONArray repoDoctorResults(Context context) throws JSONException {
        JSONArray values = new JSONArray();
        for (RepositoryRecord repository : limitedRepositories(context)) {
            JSONObject item = new JSONObject();
            item.put("repository_id", repository.repositoryId);
            item.put("name", repository.name());
            item.put("environment", repository.environment);
            item.put("doctor", RepositoryDoctor.supportSummary(context, repository));
            values.put(item);
        }
        return values;
    }

    private static JSONArray repoExecutionPlan(Context context) throws JSONException {
        JSONArray values = new JSONArray();
        for (RepositoryRecord repository : limitedRepositories(context)) {
            JSONObject doctor = RepositoryDoctor.supportSummary(context, repository);
            JSONObject plan = new JSONObject();
            plan.put("repository_id", repository.repositoryId);
            plan.put("environment", repository.environment);
            plan.put("chroot", repository.chrootName);
            plan.put("linux_user", repository.linuxUser);
            plan.put("selected_shell", doctor.optString("selected_shell", ""));
            plan.put("user_switch_method", doctor.optString("user_switch_method", ""));
            plan.put("direct_root_fallback", doctor.optBoolean("direct_root_fallback", false));
            plan.put("runuser_available", doctor.optBoolean("runuser_available", false));
            plan.put("runuser_works", doctor.optBoolean("runuser_works", false));
            plan.put("su_available", doctor.optBoolean("su_available", false));
            plan.put("su_works", doctor.optBoolean("su_works", false));
            plan.put("rejected_shell_candidates", doctor.optString("rejected_shell_candidates", ""));
            plan.put("rejected_user_switch_candidates", doctor.optString("rejected_user_switch_candidates", ""));
            plan.put("source", "RepositoryDoctor latest result; passive when doctor has not run yet");
            values.put(plan);
        }
        return values;
    }

    private static JSONArray repoPathResolution(Context context) throws JSONException {
        JSONArray values = new JSONArray();
        for (RepositoryRecord repository : limitedRepositories(context)) {
            JSONObject item = new JSONObject();
            item.put("repository_id", repository.repositoryId);
            item.put("name", repository.name());
            item.put("path_resolution", RepositoryPathResolver.supportSummary(repository));
            values.put(item);
        }
        return values;
    }

    private static JSONArray repoQueueHealth(Context context) throws JSONException {
        JSONArray values = new JSONArray();
        for (RepositoryRecord repository : limitedRepositories(context)) {
            JSONObject item = new JSONObject();
            item.put("repository_id", repository.repositoryId);
            item.put("name", repository.name());
            item.put("queue", RepoQueueRecovery.supportSummary(context, repository));
            values.put(item);
        }
        return values;
    }

    private static JSONArray recentGitOperations(Context context, String token) throws JSONException {
        JSONArray values = new JSONArray();
        int emitted = 0;
        for (RepositoryRecord repository : RepositoryStore.all(context)) {
            for (RepositoryOperationRecord operation : DevtoolDatabase.get(context)
                    .repositoryOperations(repository.repositoryId, MAX_OPERATIONS_PER_REPOSITORY)) {
                JSONObject value = new JSONObject();
                value.put("repository_id", repository.repositoryId);
                value.put("repository_name", repository.name());
                value.put("operation_id", operation.operationId);
                value.put("run_id", operation.runId);
                value.put("type", operation.type);
                value.put("target", redact(operation.target, token));
                value.put("state", operation.state);
                value.put("progress_label", operation.progressLabel);
                value.put("summary", truncate(redact(operation.summary, token)));
                value.put("queued_at", operation.queuedAt);
                value.put("started_at", operation.startedAt);
                value.put("finished_at", operation.finishedAt);
                value.put("exit_code", operation.exitCode);
                value.put("recovery_hint", RepoQueueRecovery.recoveryHint(operation));
                value.put("git_operation_trace", GitOperationTrace.supportSummary(context, operation, token));
                values.put(value);
                emitted++;
                if (emitted >= MAX_RECENT_GIT_OPERATIONS) return values;
            }
        }
        return values;
    }

    private static JSONObject validationHealth(Context context) throws JSONException {
        ValidationSessionRecord active = ValidationStore.active(context);
        ValidationSessionRecord latest = ValidationStore.latest(context);
        JSONObject value = new JSONObject();
        value.put("active_present", active != null);
        value.put("latest_present", latest != null);
        String token = BridgeSecrets.getOrCreateToken(context);
        value.put("active", validationSession(active, token));
        value.put("latest", validationSession(latest, token));
        JSONArray events = new JSONArray();
        if (latest != null && latest.sessionId != null && !latest.sessionId.isEmpty()) {
            for (DebugEventRecord event : DevtoolDatabase.get(context).recentDebugEvents(MAX_DEBUG_EVENTS)) {
                if (latest.sessionId.equals(event.validationSessionId) || "validation".equals(event.category)) {
                    events.put(debugEvent(event, token, true));
                    if (events.length() >= 48) break;
                }
            }
        }
        value.put("recent_events", events);
        return value;
    }

    private static JSONObject validationSession(ValidationSessionRecord session, String token) throws JSONException {
        JSONObject value = new JSONObject();
        if (session == null) {
            value.put("present", false);
            return value;
        }
        value.put("present", true);
        value.put("session_id", session.sessionId);
        value.put("revision", session.revision);
        value.put("state", session.state);
        value.put("effective_state", session.effectiveState);
        value.put("project_type", session.projectType);
        value.put("target", session.target);
        value.put("executor_kind", session.executorKind);
        value.put("executor_name", session.executorName);
        value.put("executor_linux_user", session.executorLinuxUser);
        value.put("completed_phases", session.completedPhases);
        value.put("phase_total", session.phaseTotal);
        value.put("overall_progress", session.overallProgress());
        value.put("last_error", truncate(redact(session.lastError, token)));
        value.put("last_message", truncate(redact(session.lastMessage, token)));
        value.put("last_reason", session.lastReason);
        value.put("recommended_retry_profile", session.recommendedRetryProfile);
        value.put("final_verdict", session.finalVerdict);
        value.put("report_warnings", session.reportWarnings);
        value.put("report_errors", session.reportErrors);
        return value;
    }

    private static JSONObject notificationHealth(Context context) throws JSONException {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        JSONObject value = new JSONObject();
        value.put("notifications_enabled", manager != null && manager.areNotificationsEnabled());
        value.put("overlay_permission", OverlayHudService.hasPermission(context));
        value.put("overlay_running", OverlayHudService.isRunning());
        value.put("current_run_notification_possible", manager != null && manager.areNotificationsEnabled());
        value.put("debug_notification_channel", DebugNotificationController.CHANNEL_ID);
        value.put("debug_notification_group", DebugNotificationController.GROUP_KEY);
        value.put("debug_notification_safe_actions", "Open Debug, Open Doctor, Open Validation, Copy Report, Copy Trace, Export Bundle, Run Probe");
        value.put("safe_actions_only", true);
        return value;
    }

    private static JSONObject redactionSelfTest(String token) throws JSONException {
        String bridgeToken = token == null || token.isEmpty() ? "sample-token" : token;
        String bearerValue = "Bearer " + "abcdef";
        String credentialPair = "user" + ":" + "pass";
        String secretValue = "hunter" + "2";
        String privateKeyMarker = "BEGIN " + "PRIVATE KEY";
        String sample = "token=" + bridgeToken
                + " " + ("Auth" + "orization") + ": " + bearerValue
                + " https://" + credentialPair + "@example.invalid/repo.git"
                + " " + ("pass" + "word") + "=" + secretValue
                + " -----" + privateKeyMarker + "-----\nabc\n-----END " + "PRIVATE KEY-----";
        String redacted = SupportRedactor.redact(sample, token);
        boolean bridgeTokenRedacted = token == null || token.isEmpty() || !redacted.contains(token);
        boolean authRedacted = !redacted.contains(bearerValue) && !redacted.contains(credentialPair)
                && !redacted.contains(secretValue) && !redacted.contains(privateKeyMarker);
        JSONObject value = new JSONObject();
        value.put("status", bridgeTokenRedacted && authRedacted ? "pass" : "fail");
        value.put("bridge_token_redacted", bridgeTokenRedacted);
        value.put("auth_header_redacted", !redacted.contains(bearerValue));
        value.put("url_credentials_redacted", !redacted.contains(credentialPair));
        value.put("secret_assignment_redacted", !redacted.contains(secretValue));
        value.put("private_key_redacted", !redacted.contains(privateKeyMarker));
        value.put("sample_output", redacted);
        return value;
    }

    private static List<RepositoryRecord> limitedRepositories(Context context) {
        List<RepositoryRecord> repositories = RepositoryStore.all(context);
        int limit = Math.min(MAX_BUNDLE_REPOSITORIES, repositories.size());
        return repositories.subList(0, limit);
    }

    private static String packageVersionName(Context context) {
        try {
            PackageInfo info = context.getPackageManager().getPackageInfo(context.getPackageName(), 0);
            return info.versionName == null ? "" : info.versionName;
        } catch (PackageManager.NameNotFoundException error) {
            return "";
        }
    }

    private static long packageVersionCode(Context context) {
        try {
            PackageInfo info = context.getPackageManager().getPackageInfo(context.getPackageName(), 0);
            return Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
        } catch (PackageManager.NameNotFoundException error) {
            return 0L;
        }
    }

    private static String redact(String value, String token) {
        return SupportRedactor.redact(value, token);
    }

    private static String truncate(String value) {
        String clean = value == null ? "" : value;
        if (clean.length() <= MAX_TEXT) return clean;
        return clean.substring(0, MAX_TEXT) + "… <truncated>";
    }
}
