package io.github.mikeceead.devtoolhud;

import org.json.JSONException;
import org.json.JSONObject;

final class RunRecord {
    String runId = "";
    String title = "Devtool run";
    String status = "waiting";
    String phase = "Preparing";
    String detail = "";
    String repo = "";
    String projectType = "auto";
    String executionEnvironment = "auto";
    String workdir = "~/";
    String target = "";
    String command = "";
    String summary = "";
    String stdout = "";
    String stderr = "";
    String error = "";
    String launchLog = "";
    String launchMode = "";
    String repositoryId = "";
    String artifact = "";
    String commitHash = "";
    String transactionBackup = "";
    String logPath = "";
    String logSourceId = "";
    int exitCode = -1;
    long lastProgressAtMs = 0L;
    long generation = 0L;
    long bridgeSequence = -1L;
    int progress = 0;
    int current = -1;
    int total = -1;
    String unitLabel = "items";
    int warnings = 0;
    int errors = 0;
    String stepsJson = "";
    boolean indeterminate = true;
    boolean pinned;
    long startedAtMs = System.currentTimeMillis();
    long updatedAtMs = System.currentTimeMillis();
    long durationMs = 0L;

    boolean terminal() {
        return status.equals("ok") || status.equals("failed") || status.equals("cancelled")
                || status.equals("interrupted") || status.equals("skipped");
    }

    JSONObject toJson() throws JSONException {
        JSONObject value = new JSONObject();
        value.put("run_id", runId);
        value.put("title", title);
        value.put("status", status);
        value.put("phase", phase);
        value.put("detail", detail);
        value.put("repo", repo);
        value.put("project_type", projectType);
        value.put("execution_environment", executionEnvironment);
        value.put("workdir", workdir);
        value.put("target", target);
        value.put("command", command);
        value.put("summary", summary);
        value.put("stdout", stdout);
        value.put("stderr", stderr);
        value.put("error", error);
        value.put("launch_log", launchLog);
        value.put("launch_mode", launchMode);
        value.put("repository_id", repositoryId);
        value.put("artifact", artifact);
        value.put("commit_hash", commitHash);
        value.put("transaction_backup", transactionBackup);
        value.put("log_path", logPath);
        value.put("log_source_id", logSourceId);
        value.put("exit_code", exitCode);
        value.put("last_progress_at_ms", lastProgressAtMs);
        value.put("generation", generation);
        value.put("bridge_sequence", bridgeSequence);
        value.put("progress", progress);
        value.put("current", current);
        value.put("total", total);
        value.put("unit_label", unitLabel);
        value.put("warnings", warnings);
        value.put("errors", errors);
        value.put("steps_json", stepsJson);
        value.put("indeterminate", indeterminate);
        value.put("pinned", pinned);
        value.put("started_at_ms", startedAtMs);
        value.put("updated_at_ms", updatedAtMs);
        value.put("duration_ms", durationMs);
        return value;
    }

    static RunRecord fromJson(JSONObject value) {
        RunRecord record = new RunRecord();
        record.runId = value.optString("run_id", "");
        record.title = value.optString("title", "Devtool run");
        record.status = value.optString("status", "waiting");
        record.phase = value.optString("phase", "Preparing");
        record.detail = HudUpdate.plainText(value.optString("detail", ""));
        record.repo = value.optString("repo", "");
        record.projectType = value.optString("project_type", "auto");
        record.executionEnvironment = value.optString("execution_environment", "auto");
        record.workdir = value.optString("workdir", "~/");
        record.target = value.optString("target", "");
        record.command = value.optString("command", "");
        record.summary = HudUpdate.plainText(value.optString("summary", ""));
        record.stdout = HudUpdate.plainText(value.optString("stdout", ""));
        record.stderr = HudUpdate.plainText(value.optString("stderr", ""));
        record.error = HudUpdate.plainText(value.optString("error", ""));
        record.launchLog = value.optString("launch_log", "");
        record.launchMode = value.optString("launch_mode", "");
        record.repositoryId = value.optString("repository_id", "");
        record.artifact = value.optString("artifact", "");
        record.commitHash = value.optString("commit_hash", "");
        record.transactionBackup = value.optString("transaction_backup", "");
        record.logPath = value.optString("log_path", "");
        record.logSourceId = value.optString("log_source_id", "");
        record.exitCode = value.optInt("exit_code", -1);
        record.lastProgressAtMs = value.optLong("last_progress_at_ms", 0L);
        record.generation = value.optLong("generation", 0L);
        record.bridgeSequence = value.optLong("bridge_sequence", -1L);
        record.progress = value.optInt("progress", 0);
        record.current = value.optInt("current", -1);
        record.total = value.optInt("total", -1);
        record.unitLabel = value.optString("unit_label", "items");
        record.warnings = value.optInt("warnings", 0);
        record.errors = value.optInt("errors", 0);
        record.stepsJson = value.optString("steps_json", "");
        record.indeterminate = value.optBoolean("indeterminate", true);
        record.pinned = value.optBoolean("pinned", false);
        record.startedAtMs = value.optLong("started_at_ms", System.currentTimeMillis());
        record.updatedAtMs = value.optLong("updated_at_ms", record.startedAtMs);
        record.durationMs = value.optLong("duration_ms", 0L);
        return record;
    }
}
