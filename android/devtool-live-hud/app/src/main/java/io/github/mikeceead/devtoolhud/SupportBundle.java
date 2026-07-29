package io.github.mikeceead.devtoolhud;

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

    private SupportBundle() {}

    static String build(Context context) {
        Context app = context.getApplicationContext();
        JSONObject root = new JSONObject();
        String token = BridgeSecrets.getOrCreateToken(app);
        try {
            root.put("schema", 1);
            root.put("kind", "devtool_android_support_bundle");
            root.put("generated_at", DateFormat.getDateTimeInstance().format(new Date()));
            root.put("privacy", privacy(app));
            root.put("app", app(app));
            root.put("device", device());
            root.put("root_bridge", rootBridge(app, token));
            root.put("bridge_roundtrip", bridgeRoundtrip(app));
            root.put("settings", settings(app));
            root.put("database", database(app));
            root.put("active_run", run(RunStore.active(app), token, AppSettings.includeSupportCommands(app)));
            root.put("recent_runs", recentRuns(app, token));
            root.put("repositories", repositories(app, token));
            root.put("notes", "Commands are omitted unless enabled in Settings. Included text is redacted for bridge tokens, HTTPS credentials, authorization headers and common secret assignments.");
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
