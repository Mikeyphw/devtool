package io.github.mikeceead.devtoolhud;

import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;

import java.util.ArrayList;
import java.util.List;

final class ValidationStore {
    static final String ACTION_UPDATED = "io.github.mikeceead.devtoolhud.VALIDATION_UPDATED";
    static final String EXTRA_SESSION_ID = "validation_session_id";
    static final String EXTRA_ERROR = "validation_error";

    private ValidationStore() {}

    /** Atomically reject stale snapshots and retain immutable attempt evidence. */
    static boolean save(Context context, ValidationSessionRecord session) {
        if (session == null || session.sessionId.isEmpty()) return false;
        SQLiteDatabase db = DevtoolDatabase.get(context).getWritableDatabase();
        boolean accepted = false;
        db.beginTransaction();
        try {
            long[] storedClock = storedClock(db, session.sessionId);
            long storedRevision = storedClock[0];
            long storedUpdatedAt = storedClock[1];
            if (storedRevision >= 0 && (session.revision < storedRevision
                    || (session.revision == storedRevision && session.updatedAtMs > 0 && session.updatedAtMs < storedUpdatedAt))) {
                db.setTransactionSuccessful();
                return false;
            }
            ContentValues values = sessionValues(session);
            db.insertWithOnConflict("validation_sessions", null, values, SQLiteDatabase.CONFLICT_IGNORE);
            db.update("validation_sessions", values, "session_id=?", new String[]{session.sessionId});

            for (ValidationPhaseRecord phase : session.phases) {
                ContentValues phaseValues = phaseValues(session.sessionId, phase);
                db.insertWithOnConflict("validation_phases", null, phaseValues, SQLiteDatabase.CONFLICT_IGNORE);
                db.update("validation_phases", phaseValues, "session_id=? AND phase_key=?",
                        new String[]{session.sessionId, phase.key});
                for (ValidationAttemptRecord attempt : phase.attemptHistory) {
                    if (attempt.attemptId == null || attempt.attemptId.trim().isEmpty()) continue;
                    ContentValues attemptValues = attemptValues(session, phase, attempt);
                    db.insertWithOnConflict("validation_attempts", null, attemptValues, SQLiteDatabase.CONFLICT_IGNORE);
                    db.update("validation_attempts", attemptValues,
                            "session_id=? AND phase_key=? AND attempt_id=?",
                            new String[]{session.sessionId, phase.key, attempt.attemptId});
                }
            }
            accepted = true;
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
        if (accepted) context.sendBroadcast(new Intent(ACTION_UPDATED).setPackage(context.getPackageName())
                .putExtra(EXTRA_SESSION_ID, session.sessionId));
        return accepted;
    }

    private static long[] storedClock(SQLiteDatabase db, String sessionId) {
        try (Cursor cursor = db.query("validation_sessions", new String[]{"revision", "updated_at_ms"},
                "session_id=?", new String[]{sessionId}, null, null, null, "1")) {
            return cursor.moveToFirst() ? new long[]{cursor.getLong(0), cursor.getLong(1)} : new long[]{-1L, 0L};
        }
    }

    private static ContentValues sessionValues(ValidationSessionRecord session) {
        ContentValues values = new ContentValues();
        values.put("session_id", session.sessionId);
        values.put("revision", session.revision);
        values.put("state", session.state);
        values.put("effective_state", session.effectiveState);
        values.put("repo_root", session.repoRoot);
        values.put("project_root", session.projectRoot);
        values.put("repository_id", session.repositoryId);
        values.put("project_type", session.projectType);
        values.put("target", session.target);
        values.put("variant", session.variant);
        values.put("artifact", session.artifact);
        values.put("validation_mode", session.validationMode);
        values.put("execution_environment", session.executionEnvironment);
        values.put("executor_kind", session.executorKind);
        values.put("executor_name", session.executorName);
        values.put("executor_transport", session.executorTransport);
        values.put("executor_workdir", session.executorWorkdir);
        values.put("executor_linux_user", session.executorLinuxUser);
        values.put("failure_action", session.failureAction);
        values.put("resource_profile", session.resourceProfile);
        values.put("required_checkpoint_name", session.requiredCheckpointName);
        values.put("recommended_retry_profile", session.recommendedRetryProfile);
        values.put("transaction_path", session.transactionPath);
        values.put("paused_reason", session.pausedReason);
        values.put("invalidated_from", session.invalidatedFrom);
        values.put("last_error", session.lastError);
        values.put("last_message", session.lastMessage);
        values.put("last_reason", session.lastReason);
        values.put("latest_event_id", session.latestEventId);
        values.put("scope_mode", session.scopeMode);
        values.put("scope_through", session.scopeThrough);
        values.put("scope_selected_json", session.scopeSelectedJson);
        values.put("created_at_ms", session.createdAtMs);
        values.put("updated_at_ms", session.updatedAtMs > 0 ? session.updatedAtMs : System.currentTimeMillis());
        values.put("recovery_available", session.recoveryAvailable ? 1 : 0);
        values.put("required_checkpoint_satisfied", session.requiredCheckpointSatisfied ? 1 : 0);
        values.put("completed_phases", session.completedPhases);
        values.put("phase_total", session.phaseTotal);
        values.put("rollback_status", session.rollbackStatus);
        values.put("rollback_error", session.rollbackError);
        values.put("rollback_total_paths", session.rollbackTotalPaths);
        values.put("rollback_verified_paths", session.rollbackVerifiedPaths);
        values.put("rollback_backup_healthy", session.rollbackBackupHealthy ? 1 : 0);
        values.put("uncommitted_changes", session.uncommittedChanges ? 1 : 0);
        values.put("report_path", session.reportPath);
        values.put("report_generated_at", session.reportGeneratedAt);
        values.put("final_verdict", session.finalVerdict);
        values.put("commit_status", session.commitStatus);
        values.put("commit_hash", session.commitHash);
        values.put("initial_git_head", session.initialGitHead);
        values.put("final_git_head", session.finalGitHead);
        values.put("report_warnings", session.reportWarnings);
        values.put("report_errors", session.reportErrors);
        return values;
    }

    private static ContentValues phaseValues(String sessionId, ValidationPhaseRecord phase) {
        ContentValues values = new ContentValues();
        values.put("session_id", sessionId);
        values.put("phase_key", phase.key);
        values.put("name", phase.name);
        values.put("label", phase.label);
        values.put("phase_index", phase.index);
        values.put("phase_total", phase.total);
        values.put("status", phase.status);
        values.put("attempts", phase.attempts);
        values.put("last_verdict", phase.lastVerdict);
        values.put("current_attempt_id", phase.currentAttemptId);
        values.put("failure_summary", phase.failureSummary);
        values.put("log_path", phase.logPath);
        values.put("invalidated_reason", phase.invalidatedReason);
        values.put("dependencies_json", phase.dependenciesJson);
        values.put("input_domains_json", phase.inputDomainsJson);
        values.put("duration_ms", phase.durationMs);
        values.put("progress", phase.progress);
        values.put("progress_detail", phase.progressDetail);
        values.put("progress_completed", phase.progressCompleted);
        values.put("progress_total_value", phase.progressTotal);
        values.put("progress_unit", phase.progressUnit);
        values.put("progress_updated_at", phase.progressUpdatedAt);
        values.put("workspace", phase.workspace);
        values.put("command_json", phase.commandJson);
        values.put("signal", phase.signal);
        values.put("issues_json", phase.issuesJson);
        values.put("classification_evidence_json", phase.classificationEvidenceJson);
        values.put("supervisor_events_json", phase.supervisorEventsJson);
        values.put("input_snapshot_before_json", phase.inputSnapshotBeforeJson);
        values.put("input_snapshot_after_json", phase.inputSnapshotAfterJson);
        values.put("required", phase.required ? 1 : 0);
        values.put("supports_progress", phase.supportsProgress ? 1 : 0);
        return values;
    }

    private static ContentValues attemptValues(ValidationSessionRecord session, ValidationPhaseRecord phase,
                                                ValidationAttemptRecord attempt) {
        ContentValues values = new ContentValues();
        values.put("session_id", session.sessionId);
        values.put("phase_key", phase.key);
        values.put("attempt_id", attempt.attemptId);
        values.put("verdict", attempt.verdict);
        values.put("failure_kind", attempt.failureKind);
        values.put("category", attempt.category);
        values.put("return_code", attempt.returnCode);
        values.put("duration_ms", attempt.durationMs);
        values.put("output_tail", attempt.outputTail);
        values.put("log_path", attempt.logPath);
        values.put("started_at", attempt.startedAt);
        values.put("finished_at", attempt.finishedAt);
        values.put("recommended_profile", attempt.recommendedProfile);
        values.put("session_revision", attempt.sessionRevision > 0 ? attempt.sessionRevision : session.revision);
        values.put("signal", attempt.signal);
        values.put("argv_json", attempt.argvJson);
        values.put("issues_json", attempt.issuesJson);
        values.put("input_snapshot_before_json", attempt.inputSnapshotBeforeJson);
        values.put("input_snapshot_after_json", attempt.inputSnapshotAfterJson);
        values.put("classification_evidence_json", attempt.classificationEvidenceJson);
        values.put("supervisor_events_json", attempt.supervisorEventsJson);
        values.put("resource_profile", attempt.resourceProfile);
        values.put("retryable", attempt.retryable ? 1 : 0);
        values.put("automatic_retry", attempt.automaticRetry ? 1 : 0);
        return values;
    }

    static ValidationSessionRecord active(Context context) {
        try (Cursor cursor = DevtoolDatabase.get(context).getReadableDatabase().query(
                "validation_sessions", null,
                "state NOT IN ('completed','aborted-kept-changes','aborted-rolled-back')",
                null, null, null, "updated_at_ms DESC", "1")) {
            return cursor.moveToFirst() ? readSession(context, cursor) : latest(context);
        }
    }

    static ValidationSessionRecord latest(Context context) {
        try (Cursor cursor = DevtoolDatabase.get(context).getReadableDatabase().query(
                "validation_sessions", null, null, null, null, null, "updated_at_ms DESC", "1")) {
            return cursor.moveToFirst() ? readSession(context, cursor) : null;
        }
    }

    static ValidationSessionRecord find(Context context, String sessionId) {
        if (sessionId == null || sessionId.trim().isEmpty()) return null;
        try (Cursor cursor = DevtoolDatabase.get(context).getReadableDatabase().query(
                "validation_sessions", null, "session_id=?", new String[]{sessionId.trim()},
                null, null, null, "1")) {
            return cursor.moveToFirst() ? readSession(context, cursor) : null;
        }
    }

    static List<ValidationSessionRecord> all(Context context) {
        List<ValidationSessionRecord> result = new ArrayList<>();
        try (Cursor cursor = DevtoolDatabase.get(context).getReadableDatabase().query(
                "validation_sessions", null, null, null, null, null, "updated_at_ms DESC")) {
            while (cursor.moveToNext()) result.add(readSession(context, cursor));
        }
        return result;
    }

    static void updateScope(Context context, String sessionId, ValidationScope scope) {
        if (sessionId == null || scope == null) return;
        ContentValues values = new ContentValues();
        values.put("scope_mode", scope.mode);
        values.put("scope_through", scope.throughPhase);
        org.json.JSONArray selected = new org.json.JSONArray();
        for (String item : scope.selectedPhases) selected.put(item);
        values.put("scope_selected_json", selected.toString());
        values.put("updated_at_ms", System.currentTimeMillis());
        DevtoolDatabase.get(context).getWritableDatabase().update("validation_sessions", values,
                "session_id=?", new String[]{sessionId});
    }

    static void reportError(Context context, String sessionId, String error) {
        if (sessionId != null && !sessionId.isEmpty()) {
            ContentValues values = new ContentValues();
            values.put("last_error", error == null ? "" : error);
            values.put("updated_at_ms", System.currentTimeMillis());
            DevtoolDatabase.get(context).getWritableDatabase().update("validation_sessions", values,
                    "session_id=?", new String[]{sessionId});
        }
        context.sendBroadcast(new Intent(ACTION_UPDATED).setPackage(context.getPackageName())
                .putExtra(EXTRA_SESSION_ID, sessionId == null ? "" : sessionId)
                .putExtra(EXTRA_ERROR, error == null ? "" : error));
    }

    private static ValidationSessionRecord readSession(Context context, Cursor cursor) {
        ValidationSessionRecord value = new ValidationSessionRecord();
        value.sessionId = text(cursor, "session_id");
        value.revision = number(cursor, "revision");
        value.state = text(cursor, "state");
        value.effectiveState = text(cursor, "effective_state");
        value.repoRoot = text(cursor, "repo_root");
        value.projectRoot = text(cursor, "project_root");
        value.repositoryId = text(cursor, "repository_id");
        value.projectType = text(cursor, "project_type");
        value.target = text(cursor, "target");
        value.variant = text(cursor, "variant");
        value.artifact = text(cursor, "artifact");
        value.validationMode = text(cursor, "validation_mode");
        value.executionEnvironment = text(cursor, "execution_environment");
        value.executorKind = first(text(cursor, "executor_kind"), value.executionEnvironment);
        value.executorName = text(cursor, "executor_name");
        value.executorTransport = first(text(cursor, "executor_transport"), "termux");
        value.executorWorkdir = first(text(cursor, "executor_workdir"), value.projectRoot);
        value.executorLinuxUser = first(text(cursor, "executor_linux_user"),
                value.executionEnvironment.equals("native-termux") ? "termux" : "");
        value.failureAction = text(cursor, "failure_action");
        value.resourceProfile = text(cursor, "resource_profile");
        value.requiredCheckpointName = text(cursor, "required_checkpoint_name");
        value.recommendedRetryProfile = text(cursor, "recommended_retry_profile");
        value.transactionPath = text(cursor, "transaction_path");
        value.pausedReason = text(cursor, "paused_reason");
        value.invalidatedFrom = text(cursor, "invalidated_from");
        value.lastError = text(cursor, "last_error");
        value.lastMessage = text(cursor, "last_message");
        value.lastReason = text(cursor, "last_reason");
        value.latestEventId = text(cursor, "latest_event_id");
        value.scopeMode = text(cursor, "scope_mode");
        value.scopeThrough = text(cursor, "scope_through");
        value.scopeSelectedJson = text(cursor, "scope_selected_json");
        value.createdAtMs = number(cursor, "created_at_ms");
        value.updatedAtMs = number(cursor, "updated_at_ms");
        value.recoveryAvailable = bool(cursor, "recovery_available");
        value.requiredCheckpointSatisfied = bool(cursor, "required_checkpoint_satisfied");
        value.completedPhases = (int) number(cursor, "completed_phases");
        value.phaseTotal = (int) number(cursor, "phase_total");
        value.rollbackStatus = text(cursor, "rollback_status");
        value.rollbackError = text(cursor, "rollback_error");
        value.rollbackTotalPaths = (int) number(cursor, "rollback_total_paths");
        value.rollbackVerifiedPaths = (int) number(cursor, "rollback_verified_paths");
        value.rollbackBackupHealthy = bool(cursor, "rollback_backup_healthy");
        value.uncommittedChanges = bool(cursor, "uncommitted_changes");
        value.reportPath = text(cursor, "report_path");
        value.reportGeneratedAt = text(cursor, "report_generated_at");
        value.finalVerdict = text(cursor, "final_verdict");
        value.commitStatus = first(text(cursor, "commit_status"), "not-requested");
        value.commitHash = text(cursor, "commit_hash");
        value.initialGitHead = text(cursor, "initial_git_head");
        value.finalGitHead = text(cursor, "final_git_head");
        value.reportWarnings = (int) number(cursor, "report_warnings");
        value.reportErrors = (int) number(cursor, "report_errors");
        value.phases.addAll(readPhases(context, value.sessionId));
        return value;
    }

    private static List<ValidationPhaseRecord> readPhases(Context context, String sessionId) {
        List<ValidationPhaseRecord> result = new ArrayList<>();
        SQLiteDatabase db = DevtoolDatabase.get(context).getReadableDatabase();
        try (Cursor cursor = db.query("validation_phases", null, "session_id=?", new String[]{sessionId},
                null, null, "phase_index")) {
            while (cursor.moveToNext()) {
                ValidationPhaseRecord phase = new ValidationPhaseRecord();
                phase.sessionId = sessionId;
                phase.key = text(cursor, "phase_key");
                phase.name = text(cursor, "name");
                phase.label = text(cursor, "label");
                phase.index = (int) number(cursor, "phase_index");
                phase.total = (int) number(cursor, "phase_total");
                phase.status = text(cursor, "status");
                phase.attempts = (int) number(cursor, "attempts");
                phase.lastVerdict = text(cursor, "last_verdict");
                phase.currentAttemptId = text(cursor, "current_attempt_id");
                phase.failureSummary = text(cursor, "failure_summary");
                phase.logPath = text(cursor, "log_path");
                phase.invalidatedReason = text(cursor, "invalidated_reason");
                phase.dependenciesJson = text(cursor, "dependencies_json");
                phase.inputDomainsJson = text(cursor, "input_domains_json");
                phase.durationMs = number(cursor, "duration_ms");
                phase.progress = (int) number(cursor, "progress");
                phase.progressDetail = text(cursor, "progress_detail");
                phase.progressCompleted = (int) numberDefault(cursor, "progress_completed", -1);
                phase.progressTotal = (int) numberDefault(cursor, "progress_total_value", -1);
                phase.progressUnit = text(cursor, "progress_unit");
                phase.progressUpdatedAt = text(cursor, "progress_updated_at");
                phase.workspace = text(cursor, "workspace");
                phase.commandJson = text(cursor, "command_json");
                phase.signal = (int) number(cursor, "signal");
                phase.issuesJson = text(cursor, "issues_json");
                phase.classificationEvidenceJson = text(cursor, "classification_evidence_json");
                phase.supervisorEventsJson = text(cursor, "supervisor_events_json");
                phase.inputSnapshotBeforeJson = text(cursor, "input_snapshot_before_json");
                phase.inputSnapshotAfterJson = text(cursor, "input_snapshot_after_json");
                phase.required = bool(cursor, "required");
                phase.supportsProgress = bool(cursor, "supports_progress");
                phase.attemptHistory.addAll(readAttempts(db, sessionId, phase.key));
                result.add(phase);
            }
        }
        return result;
    }

    private static List<ValidationAttemptRecord> readAttempts(SQLiteDatabase db, String sessionId, String phaseKey) {
        List<ValidationAttemptRecord> result = new ArrayList<>();
        try (Cursor cursor = db.query("validation_attempts", null,
                "session_id=? AND phase_key=?", new String[]{sessionId, phaseKey},
                null, null, "started_at, rowid")) {
            while (cursor.moveToNext()) {
                ValidationAttemptRecord attempt = new ValidationAttemptRecord();
                attempt.phaseKey = phaseKey;
                attempt.attemptId = text(cursor, "attempt_id");
                attempt.verdict = text(cursor, "verdict");
                attempt.failureKind = text(cursor, "failure_kind");
                attempt.category = text(cursor, "category");
                attempt.returnCode = (int) numberDefault(cursor, "return_code", -1);
                attempt.durationMs = number(cursor, "duration_ms");
                attempt.outputTail = text(cursor, "output_tail");
                attempt.logPath = text(cursor, "log_path");
                attempt.startedAt = text(cursor, "started_at");
                attempt.finishedAt = text(cursor, "finished_at");
                attempt.recommendedProfile = text(cursor, "recommended_profile");
                attempt.sessionRevision = number(cursor, "session_revision");
                attempt.signal = (int) number(cursor, "signal");
                attempt.argvJson = text(cursor, "argv_json");
                attempt.issuesJson = text(cursor, "issues_json");
                attempt.inputSnapshotBeforeJson = text(cursor, "input_snapshot_before_json");
                attempt.inputSnapshotAfterJson = text(cursor, "input_snapshot_after_json");
                attempt.classificationEvidenceJson = text(cursor, "classification_evidence_json");
                attempt.supervisorEventsJson = text(cursor, "supervisor_events_json");
                attempt.resourceProfile = text(cursor, "resource_profile");
                attempt.retryable = bool(cursor, "retryable");
                attempt.automaticRetry = bool(cursor, "automatic_retry");
                result.add(attempt);
            }
        }
        return result;
    }

    private static String text(Cursor cursor, String column) {
        int index = cursor.getColumnIndex(column);
        return index < 0 || cursor.isNull(index) ? "" : cursor.getString(index);
    }

    private static long number(Cursor cursor, String column) { return numberDefault(cursor, column, 0L); }

    private static long numberDefault(Cursor cursor, String column, long fallback) {
        int index = cursor.getColumnIndex(column);
        return index < 0 || cursor.isNull(index) ? fallback : cursor.getLong(index);
    }

    private static boolean bool(Cursor cursor, String column) { return number(cursor, column) != 0L; }

    private static String first(String... values) {
        for (String value : values) if (value != null && !value.trim().isEmpty()) return value.trim();
        return "";
    }
}
