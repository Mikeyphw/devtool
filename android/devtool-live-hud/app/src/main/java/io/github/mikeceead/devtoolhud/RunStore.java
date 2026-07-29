package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

final class RunStore {
    static final String ACTION_UPDATED = "io.github.mikeceead.devtoolhud.RUN_STORE_UPDATED";
    private static final String PREFS = "run_store";
    private static final String KEY_RUNS = "runs";
    private static final String KEY_MIGRATED = "sqlite_migrated_v1";
    private static final int MAX_RUNS = 500;
    private static final int MAX_OUTPUT = 512_000;
    private static final int MAX_LAUNCH_LOG = 96_000;
    private static final long ACTIVE_STALE_MS = 12L * 60L * 60L * 1000L;

    private RunStore() {}

    static synchronized void started(Context context, String runId, String command, String workdir) {
        started(context, runId, command, workdir, true, "root bridge");
    }

    static synchronized void started(Context context, String runId, String command, String workdir,
                                     boolean background, String shell) {
        migrateLegacy(context);
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
        linkRepository(context, record, true);
        appendLine(record, "RunCommand request created");
        appendLine(record, "mode=" + record.launchMode + " shell=" + shell + " workdir=" + workdir);
        appendLine(record, "run_id=" + runId + " waiting for authenticated HUD bridge updates");
        upsert(context, record);
        addEntry(context, record, "info", "execution", "android_app", "event",
                "Run requested", record.launchLog);
        DebugRecorder.info(context, "run", "RUNSTORE_STARTED", "RunStore",
                record.runId, record.repositoryId, "", "", "Run requested",
                DebugRecorder.details("workdir", record.workdir, "launch_mode", record.launchMode,
                        "shell", shell, "command_length", command == null ? 0 : command.length()));
    }

    static synchronized void update(Context context, HudUpdate update) {
        migrateLegacy(context);
        RunRecord record = find(context, update.runId);
        if (record != null && record.terminal() && !update.isTerminal()) return;
        if (record == null) record = new RunRecord();
        String previousPhase = record.phase;
        int previousProgress = record.progress;
        String previousStatus = record.status;
        long previousLastProgressAtMs = record.lastProgressAtMs;
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
        record.artifact = update.artifact;
        record.commitHash = update.commit;
        record.progress = update.displayProgress();
        record.current = update.current;
        record.total = update.total;
        record.unitLabel = update.unitLabel;
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
        boolean repositoryEvent = !previousPhase.equals(update.phase)
                || !previousStatus.equals(update.status)
                || (update.isTerminal() && !terminalStatus(previousStatus));
        linkRepository(context, record, repositoryEvent);
        boolean meaningfulLog = repositoryEvent
                || previousProgress / 10 != update.displayProgress() / 10;
        if (meaningfulLog) {
            String message = "bridge seq=" + update.sequence + " phase=" + update.phase
                    + " overall=" + update.displayProgress() + "% local=" + update.current + "/" + update.total;
            appendLine(record, message);
            addEntry(context, record, severityFor(record), "execution", "devtool", "event",
                    update.detail.isEmpty() ? message : update.detail, message);
        }
        if (previousLastProgressAtMs <= 0L && record.lastProgressAtMs > 0L) {
            appendTraceCheckpoint(context, record, RunCommandTrace.FIRST_PROGRESS_EVENT_RECEIVED,
                    "First authenticated bridge progress received",
                    DebugRecorder.details("sequence", update.sequence, "status", update.status,
                            "phase", update.phase, "progress", record.progress));
        }
        if (!previousStatus.equals("running") && update.status.equals("running")) {
            appendTraceCheckpoint(context, record, RunCommandTrace.RUN_ACTIVE,
                    "Run entered active execution state",
                    DebugRecorder.details("sequence", update.sequence, "phase", update.phase,
                            "progress", record.progress));
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
        addEntry(context, record, "warning", "execution", "android_app", "event",
                "Run cancelled", "Run cancellation received");
        DebugRecorder.warning(context, "run", "RUN_CANCELLED", "RunStore",
                record.runId, record.repositoryId, "", "", "Run cancellation received", "{}");
        linkRepository(context, record, true);
        upsert(context, record);
    }

    static synchronized void interrupted(Context context, String runId, String detail) {
        RunRecord record = find(context, runId);
        if (record == null || record.terminal()) return;
        markInterrupted(context, record, System.currentTimeMillis(), detail);
        upsert(context, record);
        new NotificationController(context).cancel(runId);
        OverlayHudService.cancel(runId);
    }

    static synchronized void appendDiagnostic(Context context, String runId, String message) {
        RunRecord record = find(context, runId);
        if (record == null) return;
        appendLine(record, message);
        record.updatedAtMs = System.currentTimeMillis();
        String severity = classifySeverity(message);
        addEntry(context, record, severity, "diagnostic", "android_app", "event", message, message);
        DebugRecorder.record(context, severity, "diagnostic", "RUN_DIAGNOSTIC_RECORDED", "RunStore",
                record.runId, record.repositoryId, "", "", message, DebugRecorder.details("phase", record.phase));
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
        record.exitCode = exitCode;
        record.stdout = limit(HudUpdate.plainText(stdout));
        record.stderr = limit(HudUpdate.plainText(stderr));
        record.error = limit(HudUpdate.plainText(error));
        record.updatedAtMs = System.currentTimeMillis();
        record.durationMs = Math.max(0L, record.updatedAtMs - record.startedAtMs);
        String result = "Termux result received: exit=" + exitCode
                + (clean(error).isEmpty() ? "" : " error=" + clean(error));
        appendTraceCheckpoint(context, record, RunCommandTrace.TERMUX_RESULT_RECEIVED, result,
                DebugRecorder.details("exit_code", exitCode, "had_rich_completion", hadRichCompletion));
        appendLine(record, result);
        if (!record.stdout.isEmpty()) addEntry(context, record, "info", "output", "termux", "stdout",
                firstUseful(record.stdout), record.stdout);
        if (!record.stderr.isEmpty()) addEntry(context, record, ok ? "warning" : "error", "output", "termux", "stderr",
                firstUseful(record.stderr), record.stderr);
        if (!record.error.isEmpty()) addEntry(context, record, "error", "diagnostic", "android_app", "event",
                firstUseful(record.error), record.error);
        addEntry(context, record, ok ? "success" : "error", "execution", "termux", "event", result, result);
        appendTraceCheckpoint(context, record, ok ? RunCommandTrace.RUN_COMPLETED : RunCommandTrace.RUN_FAILED,
                ok ? "Run completed successfully" : "Run finished with failure",
                DebugRecorder.details("exit_code", exitCode, "had_rich_completion", hadRichCompletion));
        DebugRecorder.record(context, ok ? "success" : "error", "run", "RUN_FINISHED", "RunStore",
                record.runId, record.repositoryId, "", "", result,
                DebugRecorder.details("exit_code", exitCode, "had_rich_completion", hadRichCompletion));
        linkRepository(context, record, !hadRichCompletion);
        upsert(context, record);
        return hadRichCompletion;
    }

    static synchronized List<RunRecord> all(Context context) {
        migrateLegacy(context);
        reconcileStale(context);
        return DevtoolDatabase.get(context).allRuns();
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

    static synchronized boolean traceCheckpoint(Context context, String runId, String checkpoint,
                                                String message, String detailJson) {
        if (runId == null || runId.trim().isEmpty()) return false;
        migrateLegacy(context);
        RunRecord record = DevtoolDatabase.get(context).findRun(runId);
        if (record == null) return false;
        appendTraceCheckpoint(context, record, checkpoint, message, detailJson);
        upsert(context, record);
        return true;
    }

    static synchronized RunRecord find(Context context, String runId) {
        if (runId == null || runId.trim().isEmpty()) return null;
        migrateLegacy(context);
        return DevtoolDatabase.get(context).findRun(runId);
    }

    static synchronized List<RunRecord> forRepository(Context context, RepositoryRecord repository) {
        migrateLegacy(context);
        reconcileStale(context);
        return DevtoolDatabase.get(context).runsForRepository(repository);
    }

    static synchronized int reconcileStale(Context context) {
        migrateLegacy(context);
        long now = System.currentTimeMillis();
        long cutoff = now - ACTIVE_STALE_MS;
        int changed = 0;
        NotificationController notifications = null;
        for (RunRecord record : DevtoolDatabase.get(context).allRuns()) {
            if (record.terminal() || record.updatedAtMs >= cutoff) continue;
            markInterrupted(context, record, now, "No live update was received before the stale-run timeout.");
            DevtoolDatabase.get(context).upsertRun(record);
            if (notifications == null) notifications = new NotificationController(context);
            notifications.cancel(record.runId);
            OverlayHudService.cancel(record.runId);
            changed++;
        }
        if (changed > 0) notifyChanged(context, "");
        return changed;
    }

    static synchronized void setPinned(Context context, String runId, boolean pinned) {
        if (runId == null || runId.trim().isEmpty()) return;
        DevtoolDatabase.get(context).setRunPinned(runId, pinned);
        notifyChanged(context, runId);
    }

    static synchronized boolean remove(Context context, String runId) {
        if (runId == null || runId.trim().isEmpty()) return false;
        boolean removed = DevtoolDatabase.get(context).deleteRun(runId);
        if (removed) notifyChanged(context, runId);
        return removed;
    }

    static synchronized void clear(Context context) {
        DevtoolDatabase.get(context).clearRuns();
        notifyChanged(context, "");
    }

    private static void upsert(Context context, RunRecord value) {
        DevtoolDatabase.get(context).upsertRun(value);
        notifyChanged(context, value.runId);
    }

    private static void addEntry(Context context, RunRecord record, String severity,
                                 String category, String source, String stream,
                                 String message, String raw) {
        DevtoolDatabase.get(context).addLogEntry(record.runId, System.currentTimeMillis(), severity,
                category, source, record.phase, stream, HudUpdate.plainText(message), HudUpdate.plainText(raw));
    }

    private static void linkRepository(Context context, RunRecord record, boolean recordEvent) {
        if (clean(record.repo).isEmpty() && clean(record.workdir).isEmpty()) return;
        RepositoryLocation location = RepositoryLocation.fromRun(record);
        record.repositoryId = location.id();
        RepositoryRecord existing = DevtoolDatabase.get(context).repository(record.repositoryId);
        RepositoryRecord repository = existing == null ? new RepositoryRecord() : existing;
        repository.repositoryId = location.id();
        repository.environment = location.environment;
        repository.chrootName = location.chrootName;
        repository.linuxUser = location.linuxUser;
        repository.configuredPath = location.path;
        repository.canonicalPath = location.path;
        repository.hostPath = location.hostPath;
        repository.projectType = record.projectType;
        repository.lastUsedAt = Math.max(repository.lastUsedAt, record.updatedAtMs);
        if (!record.terminal()) repository.activeRunCount = Math.max(1, repository.activeRunCount);
        if (repository.displayName.isEmpty()) {
            String path = location.path;
            int slash = path.lastIndexOf('/');
            repository.displayName = slash >= 0 ? path.substring(slash + 1) : path;
        }
        DevtoolDatabase.get(context).upsertRepository(repository);
        if (recordEvent) {
            DevtoolDatabase.get(context).addRepositoryEvent(repository.repositoryId, record.runId,
                    eventType(record), firstUseful(record.summary, record.detail, record.title), record.updatedAtMs);
        }
    }

    private static boolean terminalStatus(String status) {
        String clean = clean(status).toLowerCase(java.util.Locale.ROOT);
        return clean.equals("ok") || clean.equals("failed") || clean.equals("cancelled")
                || clean.equals("interrupted") || clean.equals("skipped");
    }

    private static void markInterrupted(Context context, RunRecord record, long now, String detail) {
        record.status = "interrupted";
        record.phase = "Interrupted";
        record.title = "Devtool run interrupted";
        record.detail = clean(detail).isEmpty() ? "The app stopped tracking this run because it is no longer live." : detail;
        record.indeterminate = false;
        record.updatedAtMs = now;
        record.durationMs = Math.max(0L, now - record.startedAtMs);
        appendLine(record, "Run marked interrupted: " + record.detail);
        addEntry(context, record, "warning", "execution", "android_app", "event",
                "Run marked interrupted", record.detail);
        DebugRecorder.warning(context, "run", "RUN_INTERRUPTED", "RunStore",
                record.runId, record.repositoryId, "", "", record.detail,
                DebugRecorder.details("phase", record.phase));
        linkRepository(context, record, true);
    }

    private static void appendTraceCheckpoint(Context context, RunRecord record, String checkpoint,
                                              String message, String detailJson) {
        String cleanCheckpoint = RunCommandTrace.normalizeCheckpoint(checkpoint);
        String cleanMessage = clean(message).isEmpty() ? RunCommandTrace.label(cleanCheckpoint) : message;
        appendLine(record, "trace " + cleanCheckpoint + ": " + cleanMessage);
        addEntry(context, record, "info", "trace", "android_app", "event",
                cleanCheckpoint + " · " + cleanMessage,
                RunCommandTrace.enrich(cleanCheckpoint, detailJson));
        DebugRecorder.info(context, "run_trace", cleanCheckpoint, "RunStore",
                record.runId, record.repositoryId, "", "", cleanMessage,
                RunCommandTrace.enrich(cleanCheckpoint, detailJson));
    }

    private static String eventType(RunRecord record) {
        if (!record.terminal()) return "RUN_ACTIVE";
        if (record.status.equals("ok")) return "RUN_SUCCEEDED";
        if (record.status.equals("cancelled")) return "RUN_CANCELLED";
        return "RUN_FAILED";
    }

    private static void migrateLegacy(Context context) {
        SharedPreferences preferences = prefs(context);
        if (preferences.getBoolean(KEY_MIGRATED, false)) return;
        String raw = preferences.getString(KEY_RUNS, "[]");
        try {
            JSONArray values = new JSONArray(raw == null ? "[]" : raw);
            for (int i = 0; i < values.length(); i++) {
                JSONObject item = values.optJSONObject(i);
                if (item == null) continue;
                RunRecord record = RunRecord.fromJson(item);
                if (record.runId.isEmpty()) continue;
                linkRepository(context, record, true);
                DevtoolDatabase.get(context).upsertRun(record);
                if (!record.launchLog.isEmpty()) addEntry(context, record, "info", "migration",
                        "android_app", "event", "Imported legacy launch diagnostics", record.launchLog);
                if (!record.stdout.isEmpty()) addEntry(context, record, "info", "migration",
                        "termux", "stdout", firstUseful(record.stdout), record.stdout);
                if (!record.stderr.isEmpty()) addEntry(context, record, "warning", "migration",
                        "termux", "stderr", firstUseful(record.stderr), record.stderr);
                if (!record.error.isEmpty()) addEntry(context, record, "error", "migration",
                        "android_app", "event", firstUseful(record.error), record.error);
            }
        } catch (Exception ignored) {
        }
        preferences.edit().putBoolean(KEY_MIGRATED, true).apply();
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
        String stamp = new java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.ROOT)
                .format(new java.util.Date());
        String combined = record.launchLog.isEmpty() ? stamp + "  " + clean
                : record.launchLog + "\n" + stamp + "  " + clean;
        if (combined.length() > MAX_LAUNCH_LOG) {
            combined = combined.substring(combined.length() - MAX_LAUNCH_LOG);
        }
        record.launchLog = combined;
    }

    private static String severityFor(RunRecord record) {
        if (record.status.equals("failed")) return "error";
        if (record.status.equals("ok")) return "success";
        if (record.warnings > 0) return "warning";
        return "info";
    }

    private static String classifySeverity(String message) {
        String lower = clean(message).toLowerCase(java.util.Locale.ROOT);
        if (lower.contains("fatal") || lower.contains("exception") || lower.contains("error")
                || lower.contains("failed")) return "error";
        if (lower.contains("warn")) return "warning";
        if (lower.contains("success") || lower.contains("passed")) return "success";
        return "info";
    }

    private static String limit(String value) {
        String clean = value == null ? "" : value;
        if (clean.length() <= MAX_OUTPUT) return clean;
        return clean.substring(clean.length() - MAX_OUTPUT);
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
