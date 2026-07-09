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
    String workdir = "~/";
    String target = "";
    String command = "";
    String summary = "";
    String stdout = "";
    String stderr = "";
    String error = "";
    int progress = 0;
    boolean indeterminate = true;
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
        value.put("workdir", workdir);
        value.put("target", target);
        value.put("command", command);
        value.put("summary", summary);
        value.put("stdout", stdout);
        value.put("stderr", stderr);
        value.put("error", error);
        value.put("progress", progress);
        value.put("indeterminate", indeterminate);
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
        record.detail = value.optString("detail", "");
        record.repo = value.optString("repo", "");
        record.workdir = value.optString("workdir", "~/");
        record.target = value.optString("target", "");
        record.command = value.optString("command", "");
        record.summary = value.optString("summary", "");
        record.stdout = value.optString("stdout", "");
        record.stderr = value.optString("stderr", "");
        record.error = value.optString("error", "");
        record.progress = value.optInt("progress", 0);
        record.indeterminate = value.optBoolean("indeterminate", true);
        record.startedAtMs = value.optLong("started_at_ms", System.currentTimeMillis());
        record.updatedAtMs = value.optLong("updated_at_ms", record.startedAtMs);
        record.durationMs = value.optLong("duration_ms", 0L);
        return record;
    }
}
