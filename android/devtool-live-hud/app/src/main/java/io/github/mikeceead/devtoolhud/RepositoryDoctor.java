package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

final class RepositoryDoctor {
    static final String ACTION_UPDATED = "io.github.mikeceead.devtoolhud.REPOSITORY_DOCTOR_UPDATED";
    static final String EXTRA_REPOSITORY_ID = "repository_id";
    private static final String PREFS = "repository_doctor";
    private static final int MAX_OUTPUT_PREVIEW = 1200;
    private static final ExecutorService EXECUTOR = Executors.newSingleThreadExecutor();

    private RepositoryDoctor() {}

    static void runAsync(Context context, RepositoryRecord repository) {
        if (context == null || repository == null || repository.repositoryId.isEmpty()) return;
        Context app = context.getApplicationContext();
        RepositoryRecord snapshot = copy(repository);
        DebugRecorder.info(app, "repo", "REPO_DOCTOR_REQUESTED", "RepositoryDoctor",
                "", snapshot.repositoryId, "", "", "Repository doctor requested",
                DebugRecorder.details("environment", snapshot.environment, "chroot", snapshot.chrootName,
                        "linux_user", snapshot.linuxUser, "path", snapshot.canonicalPath));
        EXECUTOR.execute(() -> {
            RepositoryDoctorResult result = runNow(app, snapshot);
            save(app, result);
            String event = result.status.equals("pass") ? "REPO_DOCTOR_PASSED"
                    : result.status.equals("warn") ? "REPO_DOCTOR_WARNED" : "REPO_DOCTOR_FAILED";
            String severity = result.status.equals("pass") ? "success"
                    : result.status.equals("warn") ? "warning" : "error";
            DebugRecorder.record(app, severity, "repo", event, "RepositoryDoctor",
                    "", result.repositoryId, "", "", result.title,
                    DebugRecorder.details("issue_key", result.issueKey, "summary", result.summary,
                            "recommendation", result.recommendation, "environment", result.environment,
                            "chroot", result.chrootName, "linux_user", result.linuxUser,
                            "path", result.commandPath, "exit_code", result.exitCode,
                            "path_exists", result.pathExists, "git_available", result.gitAvailable,
                            "git_c_works", result.gitCommandWorks,
                            "runuser_available", result.runuserAvailable,
                            "runuser_works", result.runuserWorks, "su_works", result.suWorks,
                            "selected_shell", result.selectedShell,
                            "user_switch_method", result.userSwitchMethod,
                            "direct_root_fallback", result.directRootFallback,
                            "rejected_user_switch_candidates", result.rejectedUserSwitchCandidates));
            app.sendBroadcast(new Intent(ACTION_UPDATED).setPackage(app.getPackageName())
                    .putExtra(EXTRA_REPOSITORY_ID, result.repositoryId));
        });
    }

    static RepositoryDoctorResult latest(Context context, RepositoryRecord repository) {
        if (context == null || repository == null) return new RepositoryDoctorResult();
        String json = prefs(context).getString(key(repository.repositoryId), "");
        if (json == null || json.trim().isEmpty()) return RepositoryDoctorResult.passive(repository);
        try {
            JSONObject value = new JSONObject(json);
            RepositoryDoctorResult result = new RepositoryDoctorResult();
            result.populate(repository);
            result.checkedAtMs = value.optLong("checked_at_ms", 0L);
            result.status = value.optString("status", result.status);
            result.issueKey = value.optString("issue_key", result.issueKey);
            result.title = value.optString("title", result.title);
            result.summary = value.optString("summary", result.summary);
            result.recommendation = value.optString("recommendation", result.recommendation);
            result.exitCode = value.optInt("exit_code", -1);
            result.timedOut = value.optBoolean("timed_out", false);
            result.pathExists = value.optBoolean("path_exists", false);
            result.gitDirExists = value.optBoolean("git_dir_exists", false);
            result.gitAvailable = value.optBoolean("git_available", false);
            result.gitCommandWorks = value.optBoolean("git_c_works", false);
            result.shAvailable = value.optBoolean("sh_available", false);
            result.bashAvailable = value.optBoolean("bash_available", false);
            result.zshAvailable = value.optBoolean("zsh_available", false);
            result.userExists = value.optBoolean("user_exists", false);
            result.runuserAvailable = value.optBoolean("runuser_available", false);
            result.runuserWorks = value.optBoolean("runuser_works", false);
            result.suAvailable = value.optBoolean("su_available", false);
            result.suWorks = value.optBoolean("su_works", false);
            result.selectedShell = value.optString("selected_shell", "");
            result.userSwitchMethod = value.optString("user_switch_method", "");
            result.directRootFallback = value.optBoolean("direct_root_fallback", false);
            result.rejectedShellCandidates = value.optString("rejected_shell_candidates", "");
            result.rejectedUserSwitchCandidates = value.optString("rejected_user_switch_candidates", "");
            result.outputPreview = value.optString("output_preview", "");
            return result;
        } catch (JSONException ignored) {
            return RepositoryDoctorResult.passive(repository);
        }
    }

    static JSONObject supportSummary(Context context, RepositoryRecord repository) throws JSONException {
        RepositoryDoctorResult result = latest(context, repository);
        return result.toJson();
    }

    private static RepositoryDoctorResult runNow(Context context, RepositoryRecord repository) {
        RepositoryDoctorResult result = new RepositoryDoctorResult();
        result.populate(repository);
        result.checkedAtMs = System.currentTimeMillis();
        RepositoryLocation location = repository.location();
        String payload = probePayload(location);
        RootCommandBroker.CommandResult command = new RootCommandBroker(context).repositoryDoctor(location, payload, 24L);
        result.exitCode = command.exitCode;
        result.timedOut = command.timedOut;
        String output = new String(command.bytes, StandardCharsets.UTF_8);
        Map<String, String> values = parse(output);
        values.put("output_preview", compact(output));
        result.ingest(values);
        if (command.exitCode != 0 && result.status.equals("pass")) {
            result.status = "warn";
            result.issueKey = "repo_doctor_nonzero_exit";
            result.title = "Repository doctor completed with warnings";
            result.summary = "The probe gathered evidence but the executor returned a non-zero exit code.";
            result.recommendation = "Review the output preview and executor evidence before retrying Git.";
        }
        return result;
    }

    private static String probePayload(RepositoryLocation location) {
        String path = commandPath(location);
        String user = RepositoryLocation.clean(location.linuxUser);
        return "repo=" + RootCommandBroker.shellQuote(path) + "; "
                + "linux_user=" + RootCommandBroker.shellQuote(user) + "; "
                + "kv(){ printf '__DEVTOOL_REPO_DOCTOR__%s=%s\\n' \"$1\" \"$2\"; }; "
                + "kv doctor_started 1; "
                + "kv path \"$repo\"; kv linux_user \"$linux_user\"; "
                + "[ -d \"$repo\" ] && kv path_exists 1 || kv path_exists 0; "
                + "if [ -d \"$repo/.git\" ] || git -C \"$repo\" rev-parse --git-dir >/dev/null 2>&1; then kv git_dir_exists 1; else kv git_dir_exists 0; fi; "
                + "command -v git >/dev/null 2>&1 && kv git_available 1 || kv git_available 0; "
                + "rejected_shells=''; selected_shell=''; selected_shell_name=''; "
                + "for candidate in zsh bash sh; do if command -v \"$candidate\" >/dev/null 2>&1; then path=$(command -v \"$candidate\" 2>/dev/null | head -n 1); "
                + "[ -z \"$selected_shell\" ] && selected_shell=\"$path\" && selected_shell_name=\"$candidate\"; kv ${candidate}_available 1; "
                + "else kv ${candidate}_available 0; rejected_shells=\"${rejected_shells}${rejected_shells:+,}${candidate}:missing\"; fi; done; "
                + "[ -n \"$selected_shell\" ] || { selected_shell=/bin/sh; selected_shell_name=sh; rejected_shells=\"${rejected_shells}${rejected_shells:+,}fallback:/bin/sh\"; }; "
                + "kv selected_shell \"$selected_shell\"; kv selected_shell_name \"$selected_shell_name\"; kv rejected_shell_candidates \"$rejected_shells\"; "
                + "command -v runuser >/dev/null 2>&1 && kv runuser_available 1 || kv runuser_available 0; "
                + "command -v su >/dev/null 2>&1 && kv su_available 1 || kv su_available 0; rejected_switches=''; selected_method=''; "
                + "if [ -z \"$linux_user\" ] || [ \"$linux_user\" = root ]; then kv user_exists 1; kv runuser_works 1; kv su_works 1; kv user_switch_method root; kv rejected_user_switch_candidates \"\"; "
                + "else id \"$linux_user\" >/dev/null 2>&1 && kv user_exists 1 || kv user_exists 0; "
                + "if command -v runuser >/dev/null 2>&1; then if runuser -u \"$linux_user\" -- \"$selected_shell\" -c 'id -u >/dev/null' >/dev/null 2>&1; then kv runuser_works 1; selected_method=runuser; else kv runuser_works 0; rejected_switches=\"runuser:failed\"; fi; else kv runuser_works 0; rejected_switches=\"runuser:missing\"; fi; "
                + "if [ -z \"$selected_method\" ]; then if command -v su >/dev/null 2>&1; then if su - \"$linux_user\" -s \"$selected_shell\" -c 'id -u >/dev/null' >/dev/null 2>&1; then kv su_works 1; selected_method=su; else kv su_works 0; rejected_switches=\"${rejected_switches}${rejected_switches:+,}su:failed\"; fi; else kv su_works 0; rejected_switches=\"${rejected_switches}${rejected_switches:+,}su:missing\"; fi; else kv su_works 0; fi; "
                + "[ -n \"$selected_method\" ] || selected_method=direct-root; kv user_switch_method \"$selected_method\"; kv rejected_user_switch_candidates \"$rejected_switches\"; fi; "
                + "if command -v git >/dev/null 2>&1 && [ -d \"$repo\" ]; then git -C \"$repo\" status --short >/dev/null 2>&1 && kv git_c_works 1 || kv git_c_works 0; else kv git_c_works 0; fi";
    }

    private static Map<String, String> parse(String output) {
        Map<String, String> values = new LinkedHashMap<>();
        if (output == null) return values;
        for (String line : output.split("\\R")) {
            String clean = line.trim();
            if (!clean.startsWith("__DEVTOOL_REPO_DOCTOR__")) continue;
            String payload = clean.substring("__DEVTOOL_REPO_DOCTOR__".length());
            int equals = payload.indexOf('=');
            if (equals <= 0) continue;
            String key = payload.substring(0, equals).trim().toLowerCase(Locale.ROOT);
            String value = payload.substring(equals + 1).trim();
            if (!key.isEmpty()) values.put(key, value);
        }
        return values;
    }

    private static void save(Context context, RepositoryDoctorResult result) {
        try {
            prefs(context).edit().putString(key(result.repositoryId), result.toJson().toString()).apply();
        } catch (JSONException ignored) {
            // Do not fail diagnostics if the result cannot be persisted.
        }
    }

    private static SharedPreferences prefs(Context context) {
        return context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static String key(String repositoryId) { return "repo." + RepositoryLocation.clean(repositoryId); }

    private static RepositoryRecord copy(RepositoryRecord source) {
        RepositoryRecord value = new RepositoryRecord();
        value.repositoryId = source.repositoryId;
        value.displayName = source.displayName;
        value.environment = source.environment;
        value.chrootName = source.chrootName;
        value.linuxUser = source.linuxUser;
        value.configuredPath = source.configuredPath;
        value.canonicalPath = source.canonicalPath;
        value.hostPath = source.hostPath;
        value.gitRoot = source.gitRoot;
        value.branch = source.branch;
        value.upstream = source.upstream;
        value.remote = source.remote;
        value.status = source.status;
        value.projectType = source.projectType;
        value.lastError = source.lastError;
        value.dirty = source.dirty;
        value.saved = source.saved;
        value.aheadCount = source.aheadCount;
        value.behindCount = source.behindCount;
        value.activeRunCount = source.activeRunCount;
        value.lastUsedAt = source.lastUsedAt;
        value.lastScannedAt = source.lastScannedAt;
        return value;
    }

    private static String commandPath(RepositoryLocation location) {
        if (location.environment.equals("native-termux") && !location.hostPath.isEmpty()) return location.hostPath;
        return location.path;
    }

    private static String compact(String value) {
        String clean = SupportRedactor.redact(value == null ? "" : value.trim());
        clean = clean.replace('\r', ' ').replace('\n', ' ');
        return clean.length() <= MAX_OUTPUT_PREVIEW ? clean : clean.substring(0, MAX_OUTPUT_PREVIEW - 1) + "…";
    }
}
