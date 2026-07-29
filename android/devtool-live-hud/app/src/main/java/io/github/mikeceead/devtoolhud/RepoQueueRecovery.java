package io.github.mikeceead.devtoolhud;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.List;
import java.util.Locale;

final class RepoQueueRecovery {
    static final long STALE_QUEUED_MS = 15L * 60L * 1000L;
    static final long STALE_RUNNING_MS = 20L * 60L * 1000L;
    static final String BUNDLE_KEY = "repo_queue_health";
    private static final int MAX_OPERATIONS = 80;
    private static final int MAX_TEXT = 1_200;

    private RepoQueueRecovery() {}

    static QueueHealth assess(Context context, RepositoryRecord repository) {
        QueueHealth health = new QueueHealth();
        if (context == null || repository == null || repository.repositoryId.isEmpty()) {
            health.status = "unknown";
            health.summary = "Repository queue health is unavailable";
            health.recommendation = "Open the repository again after it is available.";
            return health;
        }
        long now = System.currentTimeMillis();
        List<RepositoryOperationRecord> operations = DevtoolDatabase.get(context.getApplicationContext())
                .repositoryOperations(repository.repositoryId, MAX_OPERATIONS);
        health.repositoryId = repository.repositoryId;
        health.checkedAtMs = now;
        health.status = "pass";
        health.summary = "Repository operation queue is idle";
        health.recommendation = "No recovery action is needed.";
        for (RepositoryOperationRecord operation : operations) {
            if (operation.state.equals("queued")) {
                health.queued++;
                if (operation.elapsedAt(now) >= STALE_QUEUED_MS) {
                    health.staleQueued++;
                    if (health.oldestQueued == null || operation.queuedAt < health.oldestQueued.queuedAt) {
                        health.oldestQueued = operation;
                    }
                }
            } else if (operation.state.equals("running")) {
                health.running++;
                if (operation.elapsedAt(now) >= STALE_RUNNING_MS) {
                    health.staleRunning++;
                    if (health.oldestRunning == null || operation.startedAt < health.oldestRunning.startedAt) {
                        health.oldestRunning = operation;
                    }
                }
            } else if (operation.state.equals("orphaned")) {
                health.orphaned++;
                if (health.firstOrphan == null) health.firstOrphan = operation;
            } else if (operation.terminal()) {
                health.terminal++;
            }
        }
        if (health.orphaned > 0) {
            health.status = "fail";
            health.summary = health.orphaned + " orphaned Git operation" + plural(health.orphaned)
                    + " need recovery";
            health.recommendation = "Check whether the orphan process is still alive, then terminate or mark it interrupted.";
            return health;
        }
        if (health.staleRunning > 0) {
            health.status = "warn";
            health.summary = health.staleRunning + " running Git operation" + plural(health.staleRunning)
                    + " look stale";
            health.recommendation = "Open the operation trace. Cancel live work or mark detached work interrupted only after review.";
            return health;
        }
        if (health.staleQueued > 0) {
            health.status = "warn";
            health.summary = health.staleQueued + " queued Git operation" + plural(health.staleQueued)
                    + " have waited unusually long";
            health.recommendation = "Move, remove, or retry queued work after checking the active operation.";
            return health;
        }
        if (health.running > 0 || health.queued > 0) {
            health.status = "pass";
            health.summary = health.running + " running · " + health.queued + " queued";
            health.recommendation = "Queue is active and does not need recovery yet.";
        }
        return health;
    }

    static void recordProbe(Context context, RepositoryRecord repository) {
        if (context == null || repository == null) return;
        QueueHealth health = assess(context, repository);
        String event = health.status.equals("fail") ? "REPO_QUEUE_HEALTH_FAILED"
                : health.status.equals("warn") ? "REPO_QUEUE_HEALTH_WARN"
                : "REPO_QUEUE_HEALTH_OK";
        DebugRecorder.record(context, health.status.equals("fail") ? "error" : health.status.equals("warn") ? "warning" : "success",
                "repo", event, "RepoQueueRecovery", "", repository.repositoryId, "", "",
                health.summary, health.detailsJson());
    }

    static boolean recoverable(RepositoryOperationRecord operation) {
        if (operation == null) return false;
        return operation.state.equals("orphaned") || operation.state.equals("running") || operation.state.equals("queued");
    }

    static boolean canMarkInterrupted(RepositoryOperationRecord operation) {
        if (operation == null) return false;
        return operation.state.equals("orphaned") || operation.state.equals("running");
    }

    static boolean canTerminateOrphan(RepositoryOperationRecord operation) {
        return operation != null && operation.state.equals("orphaned") && operation.cancellable;
    }

    static boolean canRetry(RepositoryOperationRecord operation) {
        if (operation == null || !operation.terminal()) return false;
        String type = clean(operation.type).toUpperCase(Locale.ROOT);
        if (type.equals("COMMIT") || type.equals("STASH")) return false;
        if ((type.equals("STAGE") || type.equals("UNSTAGE") || type.equals("DISCARD_WORKTREE")
                || type.equals("CHECKOUT") || type.equals("CREATE_BRANCH") || type.equals("POP_STASH"))
                && clean(operation.target).isEmpty()) return false;
        return true;
    }

    static String recoveryHint(RepositoryOperationRecord operation) {
        if (operation == null) return "No recovery action is available.";
        if (operation.state.equals("orphaned")) {
            return "Safe actions: check the orphan trace, terminate the detached process, or mark it interrupted.";
        }
        if (operation.state.equals("running")) {
            return "Safe actions: cancel if Android still owns it, or mark interrupted only after confirming it is detached.";
        }
        if (operation.state.equals("queued")) {
            return "Safe actions: move the queued operation, remove it from the queue, or let the current operation finish.";
        }
        if (canRetry(operation)) return "Safe action: retry queues a new operation using current repository metadata.";
        return "Retry is unavailable because the original operation needs a message or missing payload.";
    }

    static JSONObject supportSummary(Context context, RepositoryRecord repository) throws JSONException {
        QueueHealth health = assess(context, repository);
        JSONObject value = new JSONObject();
        value.put("status", health.status);
        value.put("summary", health.summary);
        value.put("recommendation", health.recommendation);
        value.put("checked_at_ms", health.checkedAtMs);
        value.put("queued", health.queued);
        value.put("running", health.running);
        value.put("orphaned", health.orphaned);
        value.put("terminal", health.terminal);
        value.put("stale_queued", health.staleQueued);
        value.put("stale_running", health.staleRunning);
        JSONArray operations = new JSONArray();
        if (context != null && repository != null) {
            for (RepositoryOperationRecord operation : DevtoolDatabase.get(context.getApplicationContext())
                    .repositoryOperations(repository.repositoryId, MAX_OPERATIONS)) {
                if (!operation.active() && !operation.state.equals("interrupted") && !operation.state.equals("failed")) continue;
                JSONObject entry = new JSONObject();
                entry.put("operation_id", operation.operationId);
                entry.put("run_id", operation.runId);
                entry.put("type", operation.type);
                entry.put("state", operation.state);
                entry.put("target_present", !clean(operation.target).isEmpty());
                entry.put("elapsed_ms", operation.elapsedAt(System.currentTimeMillis()));
                entry.put("recoverable", recoverable(operation));
                entry.put("can_mark_interrupted", canMarkInterrupted(operation));
                entry.put("can_terminate_orphan", canTerminateOrphan(operation));
                entry.put("can_retry", canRetry(operation));
                entry.put("hint", recoveryHint(operation));
                operations.put(entry);
            }
        }
        value.put("operations", operations);
        return value;
    }

    static String plainText(Context context, RepositoryRecord repository) {
        QueueHealth health = assess(context, repository);
        StringBuilder text = new StringBuilder();
        text.append("Repository queue health\n");
        text.append("repository_id=").append(repository == null ? "" : repository.repositoryId).append('\n');
        text.append("status=").append(health.status).append('\n');
        text.append("summary=").append(health.summary).append('\n');
        text.append("recommendation=").append(health.recommendation).append('\n');
        text.append("queued=").append(health.queued).append(" running=").append(health.running)
                .append(" orphaned=").append(health.orphaned).append('\n');
        if (context != null && repository != null) {
            for (RepositoryOperationRecord operation : DevtoolDatabase.get(context.getApplicationContext())
                    .repositoryOperations(repository.repositoryId, MAX_OPERATIONS)) {
                text.append(operation.operationId).append(' ')
                        .append(operation.type).append(' ')
                        .append(operation.state).append(" · ")
                        .append(recoveryHint(operation)).append('\n');
            }
        }
        String value = text.toString();
        return value.length() <= MAX_TEXT ? value : value.substring(0, MAX_TEXT);
    }

    static final class QueueHealth {
        String repositoryId = "";
        String status = "unknown";
        String summary = "";
        String recommendation = "";
        long checkedAtMs;
        int queued;
        int running;
        int orphaned;
        int terminal;
        int staleQueued;
        int staleRunning;
        RepositoryOperationRecord oldestQueued;
        RepositoryOperationRecord oldestRunning;
        RepositoryOperationRecord firstOrphan;

        String detailsJson() {
            return DebugRecorder.details("queued", queued, "running", running, "orphaned", orphaned,
                    "terminal", terminal, "stale_queued", staleQueued, "stale_running", staleRunning,
                    "recommendation", recommendation);
        }
    }

    private static String plural(int count) { return count == 1 ? "" : "s"; }
    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
