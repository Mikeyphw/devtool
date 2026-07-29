package io.github.mikeceead.devtoolhud;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.List;
import java.util.Locale;

final class GitOperationTrace {
    static final int EXIT_PATH_MISSING_BEFORE_GIT = 71;
    static final int EXIT_GIT_MISSING_BEFORE_GIT = 73;
    static final int EXIT_NOT_GIT_BEFORE_GIT = 74;
    private static final int MAX_TRACE_EVENTS = 48;
    private static final int MAX_TEXT = 1_200;

    private GitOperationTrace() {}

    static void queued(Context context, RepositoryRecord repository, RepositoryOperationRecord operation,
                       String commandDescription) {
        record(context, "info", repository, operation, "GIT_OPERATION_QUEUED",
                "Git operation queued", DebugRecorder.details("operation", operation.type,
                        "target", operation.target, "command", commandDescription,
                        "environment", repository.environment, "path", repository.canonicalPath));
    }

    static void started(Context context, RepositoryRecord repository, RepositoryOperationRecord operation,
                        String commandDescription) {
        record(context, "info", repository, operation, "GIT_OPERATION_STARTED",
                "Git operation worker started", DebugRecorder.details("operation", operation.type,
                        "target", operation.target, "command", commandDescription,
                        "environment", repository.environment, "path", repository.canonicalPath));
    }

    static void executionPlanBuilt(Context context, RepositoryLocation location, String operationId,
                                   RootExecutionPlan plan) {
        RepositoryOperationRecord operation = operation(context, operationId);
        if (operation == null) return;
        record(context, "info", location, operation, "REPO_EXECUTION_PLAN_BUILT",
                "Repository execution plan built before Git command",
                DebugRecorder.details("operation", operation.type, "environment", location.environment,
                        "path", commandPath(location), "selected_shell", plan.selectedShell,
                        "user_switch_method", plan.userSwitchMethod,
                        "direct_root_fallback", plan.directRootFallback,
                        "rejected_shell_candidates", plan.rejectedShellCandidates,
                        "rejected_user_switch_candidates", plan.rejectedUserSwitchCandidates));
    }

    static void executorPreflightFailed(Context context, RepositoryLocation location, String operationId,
                                        RootCommandBroker.CommandResult result) {
        RepositoryOperationRecord operation = operation(context, operationId);
        if (operation == null) return;
        String event = result.exitCode == EXIT_PATH_MISSING_BEFORE_GIT ? "REPO_PATH_CONFIRMATION_FAILED"
                : result.exitCode == EXIT_GIT_MISSING_BEFORE_GIT ? "GIT_BINARY_CONFIRMATION_FAILED"
                : result.exitCode == EXIT_NOT_GIT_BEFORE_GIT ? "REPO_GIT_ROOT_CONFIRMATION_FAILED"
                : "GIT_OPERATION_PREFLIGHT_FAILED";
        String message = compact(result.text(), "Git operation preflight failed before Git started");
        record(context, "error", location, operation, event, message,
                DebugRecorder.details("operation", operation.type, "environment", location.environment,
                        "path", commandPath(location), "exit_code", result.exitCode,
                        "timed_out", result.timedOut, "cancelled", result.cancelled,
                        "stage", "before_git_command"));
    }

    static void pathConfirmed(Context context, RepositoryLocation location, String operationId) {
        RepositoryOperationRecord operation = operation(context, operationId);
        if (operation == null) return;
        record(context, "success", location, operation, "REPO_PATH_CONFIRMED",
                "Repository path confirmed before Git command",
                DebugRecorder.details("operation", operation.type, "environment", location.environment,
                        "path", commandPath(location)));
    }

    static void gitBinaryConfirmed(Context context, RepositoryLocation location, String operationId) {
        RepositoryOperationRecord operation = operation(context, operationId);
        if (operation == null) return;
        record(context, "success", location, operation, "GIT_BINARY_CONFIRMED",
                "Git binary confirmed before command start",
                DebugRecorder.details("operation", operation.type, "environment", location.environment,
                        "path", commandPath(location)));
    }

    static void commandStarted(Context context, RepositoryLocation location, String operationId) {
        RepositoryOperationRecord operation = operation(context, operationId);
        if (operation == null) return;
        record(context, "info", location, operation, "GIT_COMMAND_STARTED",
                "Git command started after executor preflight",
                DebugRecorder.details("operation", operation.type, "environment", location.environment,
                        "path", commandPath(location)));
    }

    static void outputReceived(Context context, RepositoryRecord repository, RepositoryOperationRecord operation,
                               RootCommandBroker.CommandResult result) {
        if (result == null || result.bytes == null || result.bytes.length == 0) return;
        record(context, "info", repository, operation, "GIT_OUTPUT_RECEIVED",
                "Git command produced output",
                DebugRecorder.details("operation", operation.type, "output_bytes", result.bytes.length,
                        "truncated", result.truncated, "exit_code", result.exitCode));
    }

    static void finished(Context context, RepositoryRecord repository, RepositoryOperationRecord operation,
                         String state, RootCommandBroker.CommandResult result, String message) {
        String event = state.equals("ok") ? "GIT_OPERATION_FINISHED"
                : state.equals("cancelled") ? "GIT_OPERATION_CANCELLED"
                : "GIT_OPERATION_FAILED";
        String severity = state.equals("ok") ? "success" : state.equals("cancelled") ? "warning" : "error";
        record(context, severity, repository, operation, event, message,
                DebugRecorder.details("operation", operation.type, "state", state,
                        "exit_code", result == null ? -1 : result.exitCode,
                        "timed_out", result != null && result.timedOut,
                        "cancelled", result != null && result.cancelled,
                        "truncated", result != null && result.truncated));
    }

    static String preflightPayload(String repoPath) {
        String quoted = shellQuote(clean(repoPath));
        return "repo=" + quoted + "; "
                + "if [ ! -d \"$repo\" ]; then echo 'Git trace preflight: repository path does not exist before Git started'; exit "
                + EXIT_PATH_MISSING_BEFORE_GIT + "; fi; "
                + "if ! command -v git >/dev/null 2>&1; then echo 'Git trace preflight: git binary is unavailable before Git started'; exit "
                + EXIT_GIT_MISSING_BEFORE_GIT + "; fi; "
                + "if ! git -C \"$repo\" rev-parse --is-inside-work-tree >/dev/null 2>&1; then echo 'Git trace preflight: repository path is not a Git worktree before Git started'; exit "
                + EXIT_NOT_GIT_BEFORE_GIT + "; fi; "
                + "exit 0";
    }

    static boolean preflightFailed(RootCommandBroker.CommandResult result) {
        return result == null || result.cancelled || result.timedOut || result.exitCode != 0;
    }

    static JSONArray supportSummary(Context context, RepositoryOperationRecord operation, String token)
            throws JSONException {
        JSONArray values = new JSONArray();
        if (context == null || operation == null || operation.runId == null || operation.runId.isEmpty()) {
            return values;
        }
        List<DebugEventRecord> events = DevtoolDatabase.get(context)
                .debugEventsForRun(operation.runId, MAX_TRACE_EVENTS);
        for (DebugEventRecord event : events) {
            if (!"repo".equals(event.category)) continue;
            JSONObject value = new JSONObject();
            value.put("timestamp_ms", event.timestampMs);
            value.put("severity", event.severity);
            value.put("event_type", event.eventType);
            value.put("message", truncate(SupportRedactor.redact(event.message, token)));
            value.put("detail_json", truncate(SupportRedactor.redact(event.detailJson, token)));
            values.put(value);
        }
        return values;
    }

    static String plainText(Context context, RepositoryOperationRecord operation, String token) {
        if (context == null || operation == null) return "";
        StringBuilder text = new StringBuilder();
        text.append("Git operation trace\n");
        text.append("operation_id=").append(operation.operationId).append('\n');
        text.append("run_id=").append(operation.runId).append('\n');
        text.append("type=").append(operation.type).append('\n');
        text.append("state=").append(operation.state).append('\n');
        List<DebugEventRecord> events = DevtoolDatabase.get(context).debugEventsForRun(operation.runId, MAX_TRACE_EVENTS);
        for (DebugEventRecord event : events) {
            if (!"repo".equals(event.category)) continue;
            text.append(event.timestampMs).append(' ')
                    .append(event.severity.toUpperCase(Locale.ROOT)).append(' ')
                    .append(event.eventType).append(" · ")
                    .append(SupportRedactor.redact(event.message, token)).append('\n');
        }
        return text.toString();
    }

    private static void record(Context context, String severity, RepositoryRecord repository,
                               RepositoryOperationRecord operation, String eventType,
                               String message, String details) {
        if (repository == null || operation == null) return;
        record(context, severity, repository.repositoryId, operation.runId, operation.operationId,
                eventType, message, details);
    }

    private static void record(Context context, String severity, RepositoryLocation location,
                               RepositoryOperationRecord operation, String eventType,
                               String message, String details) {
        if (location == null || operation == null) return;
        record(context, severity, location.id(), operation.runId, operation.operationId,
                eventType, message, details);
    }

    private static void record(Context context, String severity, String repositoryId, String runId,
                               String operationId, String eventType, String message, String details) {
        if (context == null) return;
        long now = System.currentTimeMillis();
        String type = clean(eventType).toUpperCase(Locale.ROOT);
        String summary = compact(message, type);
        DevtoolDatabase database = DevtoolDatabase.get(context.getApplicationContext());
        database.addRepositoryEvent(repositoryId, runId, type, summary, now);
        database.addLogEntry(runId, now, severityForLog(severity), "git", "android_app",
                "Git trace", "event", type + " · " + summary, details == null ? "" : details);
        DebugRecorder.record(context, severity, "repo", type, "GitOperationTrace", runId,
                repositoryId, operationId, "", summary, details);
    }

    private static RepositoryOperationRecord operation(Context context, String operationId) {
        String id = clean(operationId);
        if (context == null || id.isEmpty()) return null;
        return DevtoolDatabase.get(context.getApplicationContext()).repositoryOperation(id);
    }

    private static String commandPath(RepositoryLocation location) {
        if (location == null) return "";
        if ("native-termux".equals(location.environment) && location.hostPath != null
                && !location.hostPath.trim().isEmpty()) return location.hostPath.trim();
        return location.path == null ? "" : location.path.trim();
    }

    private static String severityForLog(String severity) {
        String value = clean(severity).toLowerCase(Locale.ROOT);
        if (value.equals("success")) return "info";
        if (value.equals("fatal")) return "error";
        if (value.equals("warning") || value.equals("error")) return value;
        return "info";
    }

    private static String compact(String value, String fallback) {
        String text = clean(value).replace('\n', ' ').replace('\r', ' ').replaceAll("\\s+", " ");
        if (text.isEmpty()) text = fallback;
        return text.length() > 350 ? text.substring(0, 347) + "…" : text;
    }

    private static String truncate(String value) {
        String text = value == null ? "" : value;
        return text.length() <= MAX_TEXT ? text : text.substring(0, MAX_TEXT);
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }

    private static String shellQuote(String value) {
        return "'" + (value == null ? "" : value).replace("'", "'\\''") + "'";
    }
}
