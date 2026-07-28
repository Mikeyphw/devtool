package io.github.mikeceead.devtoolhud;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

final class ValidationBridgeParser {
    static final class Parsed {
        final ValidationSessionRecord session;
        final boolean accepted;
        final String action;
        final String reason;
        final String message;
        Parsed(ValidationSessionRecord session, boolean accepted, String action, String reason, String message) {
            this.session = session; this.accepted = accepted; this.action = action; this.reason = reason; this.message = message;
        }
    }

    private ValidationBridgeParser() {}

    static Parsed parse(String output) throws JSONException {
        JSONObject envelope = extractEnvelope(output);
        if (envelope.optInt("api_version", -1) != 1) throw new JSONException("Unsupported validation API version");
        JSONObject data = envelope.optJSONObject("data");
        if (data == null) data = new JSONObject();
        ValidationSessionRecord session = parseSession(data, envelope);
        return new Parsed(session, envelope.optBoolean("accepted", false), envelope.optString("action", ""),
                envelope.optString("reason", ""), envelope.optString("message", ""));
    }

    private static JSONObject extractEnvelope(String output) throws JSONException {
        String clean = output == null ? "" : output.trim();
        if (clean.isEmpty()) throw new JSONException("Empty validation response");
        try { return new JSONObject(clean); } catch (JSONException ignored) {}
        int end = clean.lastIndexOf('}');
        for (int start = clean.lastIndexOf('{', end); start >= 0; start = clean.lastIndexOf('{', start - 1)) {
            try { return new JSONObject(clean.substring(start, end + 1)); } catch (JSONException ignored) {}
        }
        throw new JSONException("Validation response did not contain a JSON envelope");
    }

    private static ValidationSessionRecord parseSession(JSONObject data, JSONObject envelope) {
        ValidationSessionRecord value = new ValidationSessionRecord();
        value.sessionId = first(data.optString("session_id", ""), envelope.optString("session_id", ""));
        value.revision = data.has("revision") ? data.optLong("revision", 0L) : envelope.optLong("revision", 0L);
        value.state = first(data.optString("status", ""), data.optString("state", ""), envelope.optString("state", "ready"));
        value.effectiveState = first(data.optString("effective_state", ""), envelope.optString("effective_state", ""), value.state);
        value.repoRoot = data.optString("repo_root", "");
        value.projectRoot = data.optString("project_root", value.repoRoot);
        value.projectType = data.optString("project_type", "auto");
        value.target = data.optString("target", "");
        value.variant = data.optString("variant", "");
        value.validationMode = data.optString("validation_mode", "standalone");
        Object artifact = data.opt("artifact");
        value.artifact = artifact instanceof JSONArray ? artifact.toString() : String.valueOf(artifact == null ? "" : artifact);
        value.failureAction = data.optString("failure_action", "pause");
        value.resourceProfile = data.optString("resource_profile", "automatic");
        value.requiredCheckpointName = first(data.optString("required_checkpoint_name", ""), data.optString("minimum_phase", "finalize"));
        value.requiredCheckpointSatisfied = data.optBoolean("required_checkpoint_satisfied", false);
        value.recoveryAvailable = data.optBoolean("recovery_available", false);
        value.recommendedRetryProfile = data.optString("recommended_retry_profile", "");
        value.transactionPath = data.optString("transaction_path", "");
        value.pausedReason = data.optString("paused_reason", "");
        value.invalidatedFrom = data.optString("invalidated_from", "");
        value.lastError = data.optString("last_error", "");
        value.latestEventId = data.optString("latest_event_id", "");
        value.lastMessage = envelope.optString("message", "");
        value.lastReason = envelope.optString("reason", "");
        value.completedPhases = data.optInt("completed_phases", 0);
        value.phaseTotal = data.optInt("phase_total", 0);
        value.uncommittedChanges = value.state.equals("validated-uncommitted") || data.optBoolean("uncommitted_changes", false);
        value.reportPath = data.optString("report_path", "");
        value.finalVerdict = data.optString("final_verdict", "");
        value.commitStatus = data.optString("commit_status", "not-requested");
        value.commitHash = data.optString("commit_hash", "");
        value.initialGitHead = data.optString("initial_git_head", "");
        value.finalGitHead = data.optString("final_git_head", "");
        parseReportSummary(value, data.optJSONObject("report_summary"));
        value.updatedAtMs = parseTimestamp(data.optString("updated_at", ""));
        if (value.updatedAtMs <= 0) value.updatedAtMs = System.currentTimeMillis();

        parseIdentity(value, data);
        parseRollback(value, data.optJSONObject("rollback"));

        JSONArray phases = data.optJSONArray("phases");
        if (phases == null) {
            JSONObject plan = data.optJSONObject("plan");
            if (plan != null) phases = plan.optJSONArray("phases");
        }
        if (phases != null) parsePhases(value, phases);
        applyEvents(value, data.optJSONArray("events"));
        if (value.phaseTotal <= 0) value.phaseTotal = value.phases.size();
        if (value.completedPhases <= 0) {
            for (ValidationPhaseRecord phase : value.phases) if (phase.passed()) value.completedPhases++;
        }
        return value;
    }


    private static void parseReportSummary(ValidationSessionRecord value, JSONObject summary) {
        if (summary == null) return;
        value.reportGeneratedAt = summary.optString("generated_at", "");
        value.finalVerdict = first(summary.optString("final_verdict", ""), value.finalVerdict);
        JSONObject diagnostics = summary.optJSONObject("diagnostics");
        if (diagnostics != null) {
            value.reportWarnings = diagnostics.optInt("warnings", 0);
            value.reportErrors = diagnostics.optInt("errors", 0);
        }
        JSONObject commit = summary.optJSONObject("commit");
        if (commit != null) {
            value.commitStatus = first(commit.optString("status", ""), value.commitStatus);
            value.commitHash = first(commit.optString("hash", ""), value.commitHash);
        }
    }

    private static void parseIdentity(ValidationSessionRecord value, JSONObject data) {
        JSONObject execution = data.optJSONObject("execution_context");
        value.executionEnvironment = first(data.optString("execution_environment", ""),
                execution == null ? "" : execution.optString("kind", ""), "native-termux");
        value.executorKind = first(execution == null ? "" : execution.optString("kind", ""), value.executionEnvironment);
        value.executorName = execution == null ? "" : execution.optString("name", "");
        value.executorTransport = first(execution == null ? "" : execution.optString("transport", ""),
                value.executorKind.contains("root") ? "root-bridge" : "termux");
        value.executorWorkdir = first(execution == null ? "" : execution.optString("workdir", ""), value.projectRoot);
        value.executorLinuxUser = first(execution == null ? "" : execution.optString("linux_user", ""),
                value.executorKind.contains("root") ? "root" : "termux");
        JSONObject repository = data.optJSONObject("repository_identity");
        if (repository != null) {
            value.repositoryId = first(repository.optString("repository_id", ""), data.optString("repository_id", ""));
            if (value.executorName.isEmpty()) value.executorName = repository.optString("chroot_name", "");
            if (value.executorLinuxUser.isEmpty()) value.executorLinuxUser = repository.optString("linux_user", "");
            if (value.repoRoot.isEmpty()) value.repoRoot = repository.optString("canonical_path", "");
        } else value.repositoryId = data.optString("repository_id", "");
        if (!value.repoRoot.isEmpty()) value.repositoryId = value.computedRepositoryId();
    }

    private static void parseRollback(ValidationSessionRecord value, JSONObject rollback) {
        if (rollback == null) return;
        value.rollbackStatus = rollback.optString("status", "");
        value.rollbackError = rollback.optString("error", "");
        value.rollbackTotalPaths = rollback.optInt("total_paths", 0);
        value.rollbackVerifiedPaths = rollback.optInt("verified_paths", 0);
        value.rollbackBackupHealthy = rollback.optBoolean("backup_healthy", false);
    }

    private static void parsePhases(ValidationSessionRecord session, JSONArray phases) {
        for (int i = 0; i < phases.length(); i++) {
            JSONObject item = phases.optJSONObject(i);
            if (item == null) continue;
            ValidationPhaseRecord phase = new ValidationPhaseRecord();
            phase.sessionId = session.sessionId;
            phase.key = item.optString("key", "phase-" + i);
            phase.name = item.optString("name", phase.key);
            phase.label = item.optString("label", human(phase.name));
            phase.index = item.optInt("index", i + 1);
            phase.total = item.optInt("total", phases.length());
            phase.status = item.optString("effective_status", item.optString("status", "pending"));
            phase.attempts = item.optInt("attempts", 0);
            phase.lastVerdict = item.optString("last_verdict", "");
            phase.currentAttemptId = item.optString("current_attempt_id", "");
            phase.durationMs = Math.round(item.optDouble("duration_seconds", 0.0) * 1000.0);
            phase.failureSummary = first(item.optString("last_interrupted_reason", ""), item.optString("invalidated_reason", ""), item.optString("output_tail", ""));
            phase.invalidatedReason = item.optString("invalidated_reason", "");
            phase.logPath = item.optString("log_path", "");
            phase.workspace = item.optString("scope", "repository");
            phase.required = item.optBoolean("required", true);
            phase.supportsProgress = item.optBoolean("supports_progress", false);
            phase.progress = phase.passed() ? 100 : item.optInt("progress_percent", item.optInt("progress", 0));
            phase.progressDetail = item.optString("progress_detail", "");
            phase.progressCompleted = item.has("progress_completed") && !item.isNull("progress_completed") ? item.optInt("progress_completed", -1) : -1;
            phase.progressTotal = item.has("progress_total") && !item.isNull("progress_total") ? item.optInt("progress_total", -1) : -1;
            phase.progressUnit = item.optString("progress_unit", "");
            phase.progressUpdatedAt = item.optString("progress_updated_at", "");
            phase.issuesJson = json(item.optJSONArray("issues"), "[]");
            JSONArray deps = item.optJSONArray("depends_on");
            JSONArray domains = item.optJSONArray("input_domains");
            phase.dependenciesJson = deps == null ? "[]" : deps.toString();
            phase.inputDomainsJson = domains == null ? "[]" : domains.toString();
            JSONArray attempts = item.optJSONArray("attempt_history");
            if (attempts != null) parseAttempts(session, phase, attempts);
            if (!phase.attemptHistory.isEmpty()) applyLatestAttempt(phase, phase.attemptHistory.get(phase.attemptHistory.size() - 1));
            session.phases.add(phase);
        }
    }

    private static void parseAttempts(ValidationSessionRecord session, ValidationPhaseRecord phase, JSONArray attempts) {
        for (int i = 0; i < attempts.length(); i++) {
            JSONObject item = attempts.optJSONObject(i);
            if (item == null) continue;
            ValidationAttemptRecord attempt = new ValidationAttemptRecord();
            attempt.phaseKey = phase.key;
            attempt.attemptId = item.optString("attempt_id", "");
            attempt.verdict = item.optString("verdict", "");
            attempt.failureKind = item.optString("failure_kind", "");
            attempt.category = item.optString("category", "");
            attempt.outputTail = item.optString("output_tail", "");
            attempt.logPath = item.optString("log_path", "");
            attempt.startedAt = item.optString("started_at", "");
            attempt.finishedAt = item.optString("finished_at", "");
            attempt.recommendedProfile = item.optString("recommended_profile", "");
            attempt.returnCode = item.has("returncode") && !item.isNull("returncode") ? item.optInt("returncode", -1) : -1;
            attempt.signal = item.has("signal") && !item.isNull("signal") ? item.optInt("signal", 0) : 0;
            attempt.durationMs = Math.round(item.optDouble("duration_seconds", 0.0) * 1000.0);
            attempt.retryable = item.optBoolean("retryable", false);
            attempt.automaticRetry = item.optBoolean("automatic_retry", false);
            attempt.sessionRevision = item.optLong("session_revision", session.revision);
            attempt.argvJson = json(item.optJSONArray("argv"), "[]");
            attempt.issuesJson = json(item.optJSONArray("issues"), "[]");
            attempt.inputSnapshotBeforeJson = json(item.optJSONObject("input_snapshot_before"), "{}");
            attempt.inputSnapshotAfterJson = json(item.optJSONObject("input_snapshot_after"), "{}");
            attempt.classificationEvidenceJson = json(item.optJSONArray("classification_evidence"), "[]");
            attempt.supervisorEventsJson = json(item.optJSONArray("supervisor_events"), "[]");
            attempt.resourceProfile = item.optString("resource_profile", "");
            phase.attemptHistory.add(attempt);
        }
    }

    private static void applyLatestAttempt(ValidationPhaseRecord phase, ValidationAttemptRecord attempt) {
        phase.commandJson = attempt.argvJson;
        phase.signal = attempt.signal;
        phase.issuesJson = attempt.issuesJson;
        phase.classificationEvidenceJson = attempt.classificationEvidenceJson;
        phase.supervisorEventsJson = attempt.supervisorEventsJson;
        phase.inputSnapshotBeforeJson = attempt.inputSnapshotBeforeJson;
        phase.inputSnapshotAfterJson = attempt.inputSnapshotAfterJson;
        if (phase.logPath.isEmpty()) phase.logPath = attempt.logPath;
        if (phase.failureSummary.isEmpty()) phase.failureSummary = attempt.outputTail;
    }

    private static void applyEvents(ValidationSessionRecord session, JSONArray events) {
        if (events == null) return;
        for (int i = 0; i < events.length(); i++) {
            JSONObject event = events.optJSONObject(i);
            if (event == null) continue;
            String eventId = event.optString("event_id", "");
            if (!eventId.isEmpty()) session.latestEventId = eventId;
            String type = event.optString("type", "");
            JSONObject data = event.optJSONObject("data");
            if (data == null) continue;
            if (type.equals("phase-progress")) {
                ValidationPhaseRecord phase = findPhase(session, data.optString("phase_key", ""));
                if (phase == null) continue;
                String attemptId = data.optString("attempt_id", "");
                if (!attemptId.isEmpty() && !phase.currentAttemptId.isEmpty() && !attemptId.equals(phase.currentAttemptId)) continue;
                phase.progress = data.optInt("percent", phase.progress);
                phase.progressDetail = data.optString("detail", phase.progressDetail);
                if (data.has("completed") && !data.isNull("completed")) phase.progressCompleted = data.optInt("completed", phase.progressCompleted);
                if (data.has("total") && !data.isNull("total")) phase.progressTotal = data.optInt("total", phase.progressTotal);
                phase.progressUnit = data.optString("unit_label", phase.progressUnit);
                phase.progressUpdatedAt = data.optString("updated_at", event.optString("timestamp", phase.progressUpdatedAt));
            } else if (type.equals("rollback-progress")) {
                session.rollbackStatus = data.optString("state", session.rollbackStatus);
                session.rollbackVerifiedPaths = data.optInt("completed_paths", data.optInt("completed", session.rollbackVerifiedPaths));
                session.rollbackTotalPaths = data.optInt("total_paths", data.optInt("total", session.rollbackTotalPaths));
                session.rollbackError = data.optString("error", session.rollbackError);
            }
        }
    }

    private static ValidationPhaseRecord findPhase(ValidationSessionRecord session, String key) {
        for (ValidationPhaseRecord phase : session.phases) if (phase.key.equals(key) || phase.name.equals(key)) return phase;
        return null;
    }

    private static long parseTimestamp(String value) {
        if (value == null || value.trim().isEmpty()) return 0L;
        try { return java.time.Instant.parse(value.trim()).toEpochMilli(); }
        catch (java.time.format.DateTimeParseException ignored) { return 0L; }
    }

    private static String json(Object value, String fallback) { return value == null ? fallback : value.toString(); }

    private static String first(String... values) {
        for (String value : values) if (value != null && !value.trim().isEmpty()) return value.trim();
        return "";
    }

    private static String human(String value) {
        String clean = value == null ? "Phase" : value.replace('-', ' ').trim();
        return clean.isEmpty() ? "Phase" : Character.toUpperCase(clean.charAt(0)) + clean.substring(1);
    }
}
