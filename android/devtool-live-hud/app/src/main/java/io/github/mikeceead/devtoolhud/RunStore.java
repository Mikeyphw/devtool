package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

final class RunStore {
    static final String ACTION_UPDATED = "io.github.mikeceead.devtoolhud.RUN_STORE_UPDATED";
    private static final String PREFS = "run_store";
    private static final String KEY_RUNS = "runs";
    private static final int MAX_RUNS = 40;
    private static final int MAX_OUTPUT = 16_000;
    private static final int MAX_LAUNCH_LOG = 12_000;
    private static final long ACTIVE_STALE_MS = 12L * 60L * 60L * 1000L;

    private RunStore() {}

    static synchronized void started(Context context, String runId, String command, String workdir) {
        started(context, runId, command, workdir, true, "root bridge");
    }

    static synchronized void started(Context context, String runId, String command, String workdir, boolean background, String shell) {
        RunRecord record = find(context, runId);
        if (record == null) record = new RunRecord();
        record.runId = runId;
        record.title = "Devtool run";
        record.status = "waiting";
        record.phase = "Preparing";
        record.detail = "Waiting for devtool progress";
        record.repo = workdir;
        record.workdir = workdir;
        record.command = command;
        record.indeterminate = true;
        record.startedAtMs = System.currentTimeMillis();
        record.updatedAtMs = record.startedAtMs;
        record.lastProgressAtMs = 0L;
        record.launchMode = background ? "background" : "foreground";
        record.launchLog = "";
        appendLine(record, "RunCommand request created");
        appendLine(record, "mode=" + record.launchMode + " shell=" + shell + " workdir=" + workdir);
        appendLine(record, "run_id=" + runId + " waiting for authenticated HUD bridge updates");
        upsert(context, record);
    }

    static synchronized void update(Context context, HudUpdate update) {
        RunRecord record = find(context, update.runId);
        if (record == null) record = new RunRecord();
        String previousPhase = record.phase;
        int previousProgress = record.progress;
        record.runId = update.runId;
        record.title = update.title();
        record.status = update.status;
        record.phase = update.phase;
        record.detail = update.detail;
        record.repo = update.repo;
        record.projectType = update.projectType;
        record.executionEnvironment = update.executionEnvironment;
        record.target = update.target;
        record.summary = update.summary;
        record.progress = update.displayProgress();
        record.warnings = update.warnings;
        record.errors = update.errors;
        record.stepsJson = update.stepsJson;
        record.indeterminate = update.indeterminate;
        record.startedAtMs = update.startedAtMs;
        record.durationMs = update.durationMs;
        record.updatedAtMs = System.currentTimeMillis();
        if (update.sequence > 0L || !update.status.equals("waiting") || !update.phase.equals("Termux")) {
            record.lastProgressAtMs = update.updatedAtMs;
        }
        boolean meaningfulLog = !previousPhase.equals(update.phase)
                || previousProgress / 10 != update.displayProgress() / 10
                || update.isTerminal();
        if (meaningfulLog) {
            appendLine(record, "bridge seq=" + update.sequence + " phase=" + update.phase
                    + " overall=" + update.displayProgress() + "% local=" + update.current + "/" + update.total);
        }
        upsert(context, record);
    }

    static synchronized void cancelled(Context context, String runId) {
        RunRecord record = find(context, runId);
        if (record == null) return;
        record.status = "cancelled";
        record.phase = "Cancelled";
        record.title = "Devtool run cancelled";
        record.indeterminate = false;
        record.updatedAtMs = System.currentTimeMillis();
        record.durationMs = Math.max(0L, record.updatedAtMs - record.startedAtMs);
        appendLine(record, "Run cancellation received");
        upsert(context, record);
    }

    static synchronized void appendDiagnostic(Context context, String runId, String message) {
        RunRecord record = find(context, runId);
        if (record == null) return;
        appendLine(record, message);
        record.updatedAtMs = System.currentTimeMillis();
        upsert(context, record);
    }

    static synchronized boolean stillPreparing(Context context, String runId) {
        RunRecord record = find(context, runId);
        return record != null && !record.terminal() && record.lastProgressAtMs <= 0L
                && (record.status.equals("waiting") || record.phase.equals("Preparing"));
    }

    static synchronized boolean finish(Context context, String runId, int exitCode,
                                       String stdout, String stderr, String error) {
        RunRecord record = find(context, runId);
        if (record == null) {
            record = new RunRecord();
            record.runId = runId;
        }
        boolean hadRichCompletion = record.terminal();
        boolean ok = exitCode == 0 && clean(error).isEmpty();
        if (!hadRichCompletion) {
            record.status = ok ? "ok" : "failed";
            record.phase = "Finished";
            record.title = ok ? "Devtool run completed" : "Devtool run failed";
            record.detail = firstUseful(error, stderr, stdout, record.detail);
            record.progress = 100;
            record.indeterminate = false;
        }
        record.stdout = limit(HudUpdate.plainText(stdout));
        record.stderr = limit(HudUpdate.plainText(stderr));
        record.error = limit(HudUpdate.plainText(error));
        record.updatedAtMs = System.currentTimeMillis();
        record.durationMs = Math.max(0L, record.updatedAtMs - record.startedAtMs);
        appendLine(record, "Termux result received: exit=" + exitCode + (clean(error).isEmpty() ? "" : " error=" + clean(error)));
        upsert(context, record);
        return hadRichCompletion;
    }

    static synchronized List<RunRecord> all(Context context) {
        List<RunRecord> result = read(context);
        result.sort(Comparator.comparingLong((RunRecord value) -> value.updatedAtMs).reversed());
        return result;
    }

    static synchronized RunRecord latest(Context context) {
        List<RunRecord> values = all(context);
        return values.isEmpty() ? null : values.get(0);
    }

    static synchronized RunRecord active(Context context) {
        long now = System.currentTimeMillis();
        for (RunRecord record : all(context)) {
            if (!record.terminal() && now - record.updatedAtMs < ACTIVE_STALE_MS) return record;
        }
        return null;
    }

    static synchronized List<RunRecord> activeAll(Context context) {
        long now = System.currentTimeMillis();
        List<RunRecord> result = new ArrayList<>();
        for (RunRecord record : all(context)) {
            if (!record.terminal() && now - record.updatedAtMs < ACTIVE_STALE_MS) result.add(record);
        }
        return result;
    }

    static synchronized RunRecord find(Context context, String runId) {
        if (runId == null || runId.trim().isEmpty()) return null;
        for (RunRecord record : read(context)) {
            if (record.runId.equals(runId)) return record;
        }
        return null;
    }

    static synchronized boolean remove(Context context, String runId) {
        if (runId == null || runId.trim().isEmpty()) return false;
        List<RunRecord> records = read(context);
        boolean removed = records.removeIf(record -> record.runId.equals(runId));
        if (!removed) return false;
        write(context, records);
        notifyChanged(context, runId);
        return true;
    }

    static synchronized void clear(Context context) {
        prefs(context).edit().remove(KEY_RUNS).apply();
        notifyChanged(context, "");
    }

    private static void upsert(Context context, RunRecord value) {
        List<RunRecord> records = read(context);
        records.removeIf(item -> item.runId.equals(value.runId));
        records.add(value);
        records.sort(Comparator.comparingLong((RunRecord item) -> item.updatedAtMs).reversed());
        if (records.size() > MAX_RUNS) records = new ArrayList<>(records.subList(0, MAX_RUNS));
        write(context, records);
        notifyChanged(context, value.runId);
    }

    private static List<RunRecord> read(Context context) {
        List<RunRecord> result = new ArrayList<>();
        String raw = prefs(context).getString(KEY_RUNS, "[]");
        try {
            JSONArray values = new JSONArray(raw == null ? "[]" : raw);
            for (int i = 0; i < values.length(); i++) {
                JSONObject item = values.optJSONObject(i);
                if (item != null) result.add(RunRecord.fromJson(item));
            }
        } catch (JSONException ignored) {
        }
        return result;
    }

    private static void write(Context context, List<RunRecord> records) {
        JSONArray values = new JSONArray();
        for (RunRecord record : records) {
            try {
                values.put(record.toJson());
            } catch (JSONException ignored) {
            }
        }
        prefs(context).edit().putString(KEY_RUNS, values.toString()).apply();
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static void notifyChanged(Context context, String runId) {
        context.sendBroadcast(new Intent(ACTION_UPDATED)
                .setPackage(context.getPackageName())
                .putExtra("run_id", runId));
    }

    private static String firstUseful(String... values) {
        for (String value : values) {
            String clean = clean(value);
            if (!clean.isEmpty()) return clean.length() > 400 ? clean.substring(0, 397) + "…" : clean;
        }
        return "Command finished";
    }

    private static void appendLine(RunRecord record, String message) {
        String clean = HudUpdate.plainText(message);
        if (clean.isEmpty()) return;
        String stamp = new java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.ROOT).format(new java.util.Date());
        String combined = record.launchLog.isEmpty() ? stamp + "  " + clean : record.launchLog + "\n" + stamp + "  " + clean;
        if (combined.length() > MAX_LAUNCH_LOG) combined = combined.substring(combined.length() - MAX_LAUNCH_LOG);
        record.launchLog = combined;
    }

    private static String limit(String value) {
        String clean = value == null ? "" : value;
        if (clean.length() <= MAX_OUTPUT) return clean;
        return clean.substring(clean.length() - MAX_OUTPUT);
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
