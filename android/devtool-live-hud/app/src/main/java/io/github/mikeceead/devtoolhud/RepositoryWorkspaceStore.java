package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.Intent;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

final class RepositoryWorkspaceStore {
    static final String ACTION_UPDATED = "io.github.mikeceead.devtoolhud.REPOSITORY_WORKSPACE_UPDATED";
    static final String EXTRA_REPOSITORY_ID = "repository_id";
    static final String EXTRA_MESSAGE = "message";

    private static final ExecutorService EXECUTOR = Executors.newFixedThreadPool(3);
    private static final Map<String, Snapshot> SNAPSHOTS = new HashMap<>();
    private static final Map<String, ArrayDeque<QueuedOperation>> QUEUES = new HashMap<>();
    private static final Map<String, ActiveOperation> ACTIVE = new HashMap<>();
    private static final Object LOCK = new Object();
    private static boolean initialized;

    private RepositoryWorkspaceStore() {}

    static void initialize(Context context) {
        Context app = context.getApplicationContext();
        synchronized (LOCK) {
            if (initialized) return;
            initialized = true;
        }
        DevtoolDatabase database = DevtoolDatabase.get(app);
        List<RepositoryOperationRecord> stale = database.activeRepositoryOperations();
        if (stale.isEmpty()) return;
        long now = System.currentTimeMillis();
        RootCommandBroker recoveryBroker = new RootCommandBroker(app);
        for (RepositoryOperationRecord operation : stale) {
            RepositoryRecord repository = database.repository(operation.repositoryId);
            boolean alive = recoveryBroker.operationProcessAlive(operation.operationId);
            if (alive && repository != null) {
                operation.state = "orphaned";
                operation.finishedAt = 0L;
                operation.exitCode = -1;
                operation.cancellable = true;
                operation.summary = "Root Git process is still running without Android app ownership";
                database.upsertRepositoryOperation(operation);
                database.addRepositoryEvent(operation.repositoryId, operation.runId,
                        "GIT_" + operation.type + "_ORPHANED", operation.summary, now);
                DebugRecorder.warning(app, "repo", "REPO_OPERATION_ORPHANED", "RepositoryWorkspaceStore",
                        operation.runId, operation.repositoryId, operation.operationId, "", operation.summary,
                        DebugRecorder.details("operation", operation.type));
                updateSyntheticRun(app, repository, operation, "waiting", operation.summary,
                        "Recovered a live root operation after Android process restart", "warning");
            } else {
                operation.state = "interrupted";
                operation.finishedAt = now;
                operation.exitCode = 130;
                operation.cancellable = false;
                operation.summary = "Android process stopped before the Git operation completed";
                database.upsertRepositoryOperation(operation);
                database.addRepositoryEvent(operation.repositoryId, operation.runId,
                        "GIT_" + operation.type + "_INTERRUPTED", operation.summary, now);
                DebugRecorder.warning(app, "repo", "REPO_OPERATION_INTERRUPTED", "RepositoryWorkspaceStore",
                        operation.runId, operation.repositoryId, operation.operationId, "", operation.summary,
                        DebugRecorder.details("operation", operation.type));
                finishSyntheticRun(app, operation, repository, "interrupted", operation.summary, "");
            }
        }
        app.sendBroadcast(new Intent(ACTION_UPDATED).setPackage(app.getPackageName()));
    }

    static Snapshot snapshot(String repositoryId) {
        synchronized (LOCK) {
            Snapshot value = SNAPSHOTS.get(clean(repositoryId));
            return value == null ? new Snapshot() : value;
        }
    }

    static boolean busy(String repositoryId) {
        String id = clean(repositoryId);
        synchronized (LOCK) {
            ArrayDeque<QueuedOperation> queue = QUEUES.get(id);
            return ACTIVE.containsKey(id) || queue != null && !queue.isEmpty();
        }
    }

    static List<RepositoryOperationRecord> operations(Context context, String repositoryId) {
        Context app = context.getApplicationContext();
        DevtoolDatabase database = DevtoolDatabase.get(app);
        List<RepositoryOperationRecord> values = database.repositoryOperations(repositoryId, 100);
        RootCommandBroker broker = null;
        boolean changed = false;
        for (RepositoryOperationRecord operation : values) {
            if (!operation.state.equals("orphaned")) continue;
            if (broker == null) broker = new RootCommandBroker(app);
            if (broker.operationProcessAlive(operation.operationId)) continue;
            long now = System.currentTimeMillis();
            operation.state = "interrupted";
            operation.finishedAt = now;
            operation.exitCode = 130;
            operation.cancellable = false;
            operation.summary = "Detached root Git process ended without a recoverable result";
            database.upsertRepositoryOperation(operation);
            database.addRepositoryEvent(operation.repositoryId, operation.runId,
                    "GIT_" + operation.type + "_INTERRUPTED", operation.summary, now);
            finishSyntheticRun(app, operation, database.repository(operation.repositoryId),
                    "interrupted", operation.summary, "");
            changed = true;
        }
        return changed ? database.repositoryOperations(repositoryId, 100) : values;
    }

    static boolean cancel(Context context, String repositoryId, String operationId) {
        Context app = context.getApplicationContext();
        String id = clean(repositoryId);
        String wanted = clean(operationId);
        QueuedOperation removed = null;
        synchronized (LOCK) {
            ActiveOperation active = ACTIVE.get(id);
            if (active != null && active.queued.record.operationId.equals(wanted)) {
                active.token.cancel();
                broadcast(app, id, "Cancelling " + active.queued.request.operation.progressLabel.toLowerCase(java.util.Locale.ROOT));
                return true;
            }
            ArrayDeque<QueuedOperation> queue = QUEUES.get(id);
            if (queue != null) {
                for (QueuedOperation candidate : queue) {
                    if (candidate.record.operationId.equals(wanted)) {
                        removed = candidate;
                        break;
                    }
                }
                if (removed != null) queue.remove(removed);
            }
        }
        if (removed == null) {
            DevtoolDatabase database = DevtoolDatabase.get(app);
            RepositoryOperationRecord orphaned = database.repositoryOperation(wanted);
            if (orphaned == null || !orphaned.state.equals("orphaned") || !orphaned.cancellable) return false;
            RepositoryRecord repository = database.repository(orphaned.repositoryId);
            DebugRecorder.warning(app, "repo", "REPO_ORPHAN_CANCEL_REQUESTED", "RepositoryWorkspaceStore",
                    orphaned.runId, orphaned.repositoryId, orphaned.operationId, "", "Cancelling orphaned repository operation",
                    DebugRecorder.details("operation", orphaned.type));
            boolean terminated = new RootCommandBroker(app).terminateOperationProcess(orphaned.operationId);
            long now = System.currentTimeMillis();
            orphaned.state = terminated ? "cancelled" : "interrupted";
            orphaned.finishedAt = now;
            orphaned.exitCode = 130;
            orphaned.cancellable = false;
            orphaned.summary = terminated ? "Orphaned root Git operation terminated"
                    : "The orphaned root process was no longer running";
            database.upsertRepositoryOperation(orphaned);
            database.addRepositoryEvent(orphaned.repositoryId, orphaned.runId,
                    "GIT_" + orphaned.type + "_" + orphaned.state.toUpperCase(java.util.Locale.ROOT),
                    orphaned.summary, now);
            finishSyntheticRun(app, orphaned, repository, orphaned.state, orphaned.summary, "");
            broadcast(app, orphaned.repositoryId, orphaned.summary);
            if (repository != null) new RepositoryNotificationController(app).show(repository, orphaned);
            return true;
        }
        completeCancelled(app, removed, "Cancelled before the operation started");
        scheduleNext(app, id);
        return true;
    }


    static boolean terminateOrphan(Context context, String repositoryId, String operationId) {
        Context app = context.getApplicationContext();
        RepositoryOperationRecord operation = DevtoolDatabase.get(app).repositoryOperation(clean(operationId));
        if (!RepoQueueRecovery.canTerminateOrphan(operation)) return false;
        DebugRecorder.warning(app, "repo", "REPO_ORPHAN_TERMINATE_REQUESTED", "RepositoryWorkspaceStore",
                operation.runId, operation.repositoryId, operation.operationId, "", "Terminate orphaned Git operation requested",
                DebugRecorder.details("operation", operation.type, "state", operation.state));
        return cancel(app, repositoryId, operationId);
    }

    static boolean markInterrupted(Context context, String repositoryId, String operationId) {
        Context app = context.getApplicationContext();
        String id = clean(repositoryId);
        String wanted = clean(operationId);
        RepositoryOperationRecord operation = DevtoolDatabase.get(app).repositoryOperation(wanted);
        if (!RepoQueueRecovery.canMarkInterrupted(operation)) return false;
        if (activeInMemory(id, wanted)) return false;
        RepositoryRecord repository = DevtoolDatabase.get(app).repository(operation.repositoryId);
        long now = System.currentTimeMillis();
        String previousState = operation.state;
        operation.state = "interrupted";
        operation.finishedAt = now;
        operation.exitCode = 130;
        operation.cancellable = false;
        operation.summary = "Marked interrupted after queue/orphan recovery review";
        DevtoolDatabase database = DevtoolDatabase.get(app);
        database.upsertRepositoryOperation(operation);
        database.addRepositoryEvent(operation.repositoryId, operation.runId,
                "GIT_" + operation.type + "_MARKED_INTERRUPTED", operation.summary, now);
        DebugRecorder.warning(app, "repo", "REPO_OPERATION_MARK_INTERRUPTED", "RepositoryWorkspaceStore",
                operation.runId, operation.repositoryId, operation.operationId, "", operation.summary,
                DebugRecorder.details("operation", operation.type, "previous_state", previousState,
                        "recovery_action", "mark_interrupted"));
        finishSyntheticRun(app, operation, repository, "interrupted", operation.summary, "");
        synchronized (LOCK) { ACTIVE.remove(operation.repositoryId); }
        broadcast(app, operation.repositoryId, operation.summary);
        if (repository != null) new RepositoryNotificationController(app).show(repository, operation);
        return true;
    }

    static boolean retry(Context context, String repositoryId, String operationId, boolean afterPathRepair) {
        Context app = context.getApplicationContext();
        RepositoryOperationRecord operation = DevtoolDatabase.get(app).repositoryOperation(clean(operationId));
        if (!RepoQueueRecovery.canRetry(operation)) {
            if (operation != null) DebugRecorder.warning(app, "repo", "REPO_OPERATION_RETRY_UNAVAILABLE",
                    "RepositoryWorkspaceStore", operation.runId, operation.repositoryId, operation.operationId, "",
                    "Git operation retry is unavailable because the original payload is incomplete or still active",
                    DebugRecorder.details("operation", operation.type, "state", operation.state,
                            "target_present", !operation.target.isEmpty()));
            return false;
        }
        RepositoryRecord repository = DevtoolDatabase.get(app).repository(operation.repositoryId);
        if (repository == null) return false;
        Request request = requestFromRecord(operation);
        if (request == null) return false;
        DebugRecorder.info(app, "repo", afterPathRepair ? "REPO_OPERATION_RETRY_AFTER_PATH_REPAIR_REQUESTED"
                        : "REPO_OPERATION_RETRY_SAME_PLAN_REQUESTED", "RepositoryWorkspaceStore",
                operation.runId, operation.repositoryId, operation.operationId, "",
                afterPathRepair ? "Retry requested after repository path repair" : "Retry requested with current repository execution plan",
                DebugRecorder.details("operation", operation.type, "target", operation.target,
                        "after_path_repair", afterPathRepair));
        enqueue(app, repository, request);
        return true;
    }

    static RepoQueueRecovery.QueueHealth queueHealth(Context context, RepositoryRecord repository) {
        RepoQueueRecovery.recordProbe(context, repository);
        return RepoQueueRecovery.assess(context, repository);
    }

    static boolean moveQueued(Context context, String repositoryId, String operationId, int direction) {
        if (direction != -1 && direction != 1) return false;
        Context app = context.getApplicationContext();
        String id = clean(repositoryId);
        String wanted = clean(operationId);
        List<QueuedOperation> reordered;
        long base = System.currentTimeMillis() * 1000L;
        synchronized (LOCK) {
            ArrayDeque<QueuedOperation> queue = QUEUES.get(id);
            if (queue == null || queue.size() < 2) return false;
            reordered = new ArrayList<>(queue);
            int index = -1;
            for (int i = 0; i < reordered.size(); i++) {
                if (reordered.get(i).record.operationId.equals(wanted)) {
                    index = i;
                    break;
                }
            }
            int target = index + direction;
            if (index < 0 || target < 0 || target >= reordered.size()) return false;
            Collections.swap(reordered, index, target);
            for (int i = 0; i < reordered.size(); i++) {
                reordered.get(i).record.queueOrder = base + i;
            }
            queue.clear();
            queue.addAll(reordered);
        }
        DevtoolDatabase database = DevtoolDatabase.get(app);
        for (QueuedOperation queued : reordered) {
            RepositoryOperationRecord record = queued.record;
            database.updateRepositoryOperationQueueOrder(record.operationId, record.queueOrder);
        }
        broadcast(app, id, direction < 0 ? "Queued operation moved earlier" : "Queued operation moved later");
        return true;
    }

    static void refreshAsync(Context context, RepositoryRecord repository) {
        if (repository == null || repository.repositoryId.isEmpty()) return;
        Context app = context.getApplicationContext();
        String id = repository.repositoryId;
        synchronized (LOCK) {
            Snapshot current = snapshotLocked(id).copy();
            if (current.loading) return;
            current.loading = true;
            current.message = "";
            SNAPSHOTS.put(id, current);
        }
        DebugRecorder.info(app, "repo", "REPO_REFRESH_REQUESTED", "RepositoryWorkspaceStore",
                "", repository.repositoryId, "", "", "Repository refresh requested",
                DebugRecorder.details("environment", repository.environment, "path", repository.canonicalPath));
        broadcast(app, id, "");
        EXECUTOR.execute(() -> {
            try {
                Snapshot next = load(app, repository);
                synchronized (LOCK) { SNAPSHOTS.put(id, next); }
                broadcast(app, id, next.message);
            } catch (RuntimeException error) {
                Snapshot failed;
                synchronized (LOCK) {
                    failed = snapshotLocked(id).copy();
                    failed.loading = false;
                    failed.message = compact(error.getMessage(), "Unable to refresh repository");
                    SNAPSHOTS.put(id, failed);
                }
                DebugRecorder.error(app, "repo", "REPO_REFRESH_FAILED", "RepositoryWorkspaceStore",
                        "", id, "", "", failed.message, DebugRecorder.details("error", error.getMessage()));
                broadcast(app, id, failed.message);
            }
        });
    }

    static void stage(Context context, RepositoryRecord repository, String path) {
        enqueue(context, repository, Request.paths(Operation.STAGE, Collections.singletonList(path)));
    }

    static void unstage(Context context, RepositoryRecord repository, String path) {
        enqueue(context, repository, Request.paths(Operation.UNSTAGE, Collections.singletonList(path)));
    }

    static void discardWorktree(Context context, RepositoryRecord repository, String path) {
        enqueue(context, repository, Request.paths(Operation.DISCARD_WORKTREE, Collections.singletonList(path)));
    }

    static void stageAll(Context context, RepositoryRecord repository) {
        enqueue(context, repository, Request.simple(Operation.STAGE_ALL));
    }

    static void unstageAll(Context context, RepositoryRecord repository) {
        enqueue(context, repository, Request.simple(Operation.UNSTAGE_ALL));
    }

    static void commit(Context context, RepositoryRecord repository, String message) {
        enqueue(context, repository, Request.value(Operation.COMMIT, message));
    }

    static void fetchAll(Context context, RepositoryRecord repository) {
        enqueue(context, repository, Request.simple(Operation.FETCH));
    }

    static void pullFastForward(Context context, RepositoryRecord repository) {
        enqueue(context, repository, Request.simple(Operation.PULL_FF));
    }

    static void push(Context context, RepositoryRecord repository) {
        enqueue(context, repository, Request.simple(Operation.PUSH));
    }

    static void checkout(Context context, RepositoryRecord repository, String branch, boolean remote) {
        enqueue(context, repository, Request.branch(Operation.CHECKOUT, branch, remote));
    }

    static void createBranch(Context context, RepositoryRecord repository, String branch) {
        enqueue(context, repository, Request.value(Operation.CREATE_BRANCH, branch));
    }

    static void stash(Context context, RepositoryRecord repository, String message) {
        enqueue(context, repository, Request.value(Operation.STASH, message));
    }

    static void popStash(Context context, RepositoryRecord repository, String reference) {
        enqueue(context, repository, Request.value(Operation.POP_STASH, reference));
    }

    static void hardReset(Context context, RepositoryRecord repository) {
        enqueue(context, repository, Request.simple(Operation.HARD_RESET));
    }

    private static void enqueue(Context context, RepositoryRecord repository, Request request) {
        if (repository == null || repository.repositoryId.isEmpty()) return;
        Context app = context.getApplicationContext();
        initialize(app);
        long now = System.currentTimeMillis();
        RepositoryOperationRecord record = new RepositoryOperationRecord();
        record.operationId = "git-op-" + UUID.randomUUID();
        record.repositoryId = repository.repositoryId;
        record.runId = "git-run-" + record.operationId.substring(7);
        record.type = request.operation.name();
        record.target = request.target();
        record.state = "queued";
        record.progressLabel = request.operation.progressLabel;
        record.summary = request.operation.queuedLabel + (record.target.isEmpty() ? "" : ": " + record.target);
        record.queuedAt = now;
        record.cancellable = true;
        QueuedOperation queued = new QueuedOperation(repository, request, record);
        int position;
        synchronized (LOCK) {
            ArrayDeque<QueuedOperation> queue = QUEUES.computeIfAbsent(repository.repositoryId,
                    ignored -> new ArrayDeque<>());
            long lastOrder = queue.isEmpty() ? now * 1000L : queue.peekLast().record.queueOrder;
            record.queueOrder = Math.max(now * 1000L, lastOrder + 1L);
            queue.addLast(queued);
            position = queue.size() + (ACTIVE.containsKey(repository.repositoryId) ? 1 : 0);
        }
        DevtoolDatabase database = DevtoolDatabase.get(app);
        database.upsertRepositoryOperation(record);
        database.addRepositoryEvent(record.repositoryId, record.runId,
                "GIT_" + record.type + "_QUEUED", record.summary, now);
        DebugRecorder.info(app, "repo", "REPO_OPERATION_QUEUED", "RepositoryWorkspaceStore",
                record.runId, record.repositoryId, record.operationId, "", record.summary,
                DebugRecorder.details("operation", record.type, "target", record.target,
                        "environment", repository.environment, "path", repository.canonicalPath));
        GitOperationTrace.queued(app, repository, record, request.commandDescription());
        createSyntheticRun(app, repository, record, request);
        broadcast(app, repository.repositoryId, position > 1
                ? request.operation.queuedLabel + " · queue position " + position : request.operation.queuedLabel);
        scheduleNext(app, repository.repositoryId);
    }

    private static void scheduleNext(Context app, String repositoryId) {
        QueuedOperation next;
        RootCommandBroker.CancellationToken token;
        synchronized (LOCK) {
            if (ACTIVE.containsKey(repositoryId)) return;
            ArrayDeque<QueuedOperation> queue = QUEUES.get(repositoryId);
            next = queue == null ? null : queue.pollFirst();
            if (next == null) {
                QUEUES.remove(repositoryId);
                return;
            }
            token = new RootCommandBroker.CancellationToken();
            ACTIVE.put(repositoryId, new ActiveOperation(next, token));
        }
        EXECUTOR.execute(() -> executeQueued(app, next, token));
    }

    private static void executeQueued(Context app, QueuedOperation queued,
                                      RootCommandBroker.CancellationToken token) {
        RepositoryOperationRecord operation = queued.record;
        long started = System.currentTimeMillis();
        operation.state = "running";
        operation.startedAt = started;
        operation.summary = queued.request.operation.progressLabel;
        DevtoolDatabase database = DevtoolDatabase.get(app);
        database.upsertRepositoryOperation(operation);
        database.addRepositoryEvent(operation.repositoryId, operation.runId,
                "GIT_" + operation.type + "_STARTED", operation.summary, started);
        DebugRecorder.info(app, "repo", "REPO_OPERATION_STARTED", "RepositoryWorkspaceStore",
                operation.runId, operation.repositoryId, operation.operationId, "", operation.summary,
                DebugRecorder.details("operation", operation.type, "environment", queued.repository.environment,
                        "path", queued.repository.canonicalPath));
        GitOperationTrace.started(app, queued.repository, operation, queued.request.commandDescription());
        updateSyntheticRun(app, queued.repository, operation, "waiting", operation.summary,
                "Git operation started", "info");
        broadcast(app, operation.repositoryId, operation.summary);

        RootCommandBroker.CommandResult result;
        String message;
        String state;
        try {
            RootCommandBroker broker = new RootCommandBroker(app, token, operation.operationId);
            result = perform(broker, queued.repository, queued.request);
            GitOperationTrace.outputReceived(app, queued.repository, operation, result);
            String target = queued.request.target();
            if (result.cancelled) {
                state = "cancelled";
                message = queued.request.operation.cancelledLabel
                        + (target.isEmpty() ? "" : ": " + target);
            } else if (result.exitCode == 0 && !result.timedOut) {
                state = "ok";
                String detail = safeDetail(result.text());
                message = queued.request.operation.successLabel
                        + (target.isEmpty() ? "" : ": " + target)
                        + (detail.isEmpty() ? "" : " · " + detail);
            } else {
                state = "failed";
                String detail = compact(result.timedOut ? "Operation timed out" : safeDetail(result.text()),
                        "Git operation failed");
                message = queued.request.operation.failureLabel
                        + (target.isEmpty() ? "" : " for " + target) + ": " + detail;
            }
        } catch (RuntimeException error) {
            result = RootCommandBroker.CommandResult.error(error.getMessage());
            state = token.cancelled() ? "cancelled" : "failed";
            message = state.equals("cancelled") ? queued.request.operation.cancelledLabel
                    : queued.request.operation.failureLabel + ": "
                        + compact(error.getMessage(), "Unexpected repository error");
        }

        DebugRecorder.record(app, state.equals("ok") ? "success" : state.equals("cancelled") ? "warning" : "error",
                "repo", state.equals("ok") ? "REPO_OPERATION_SUCCEEDED" : state.equals("cancelled") ? "REPO_OPERATION_CANCELLED" : "REPO_OPERATION_FAILED",
                "RepositoryWorkspaceStore", operation.runId, operation.repositoryId, operation.operationId, "", message,
                DebugRecorder.details("operation", operation.type, "exit_code", result.exitCode,
                        "timed_out", result.timedOut, "cancelled", result.cancelled));
        GitOperationTrace.finished(app, queued.repository, operation, state, result, message);
        operation.state = state;
        operation.finishedAt = System.currentTimeMillis();
        operation.exitCode = result.exitCode;
        operation.cancellable = false;
        operation.summary = message;
        database.upsertRepositoryOperation(operation);
        String suffix = state.equals("ok") ? "SUCCEEDED" : state.equals("cancelled") ? "CANCELLED" : "FAILED";
        database.addRepositoryEvent(operation.repositoryId, operation.runId,
                "GIT_" + operation.type + "_" + suffix, message, operation.finishedAt);
        finishSyntheticRun(app, operation, queued.repository, state, message, redactOutput(result.text()));

        Snapshot next;
        try {
            next = load(app, queued.repository);
            next.message = message;
        } catch (RuntimeException error) {
            next = snapshot(operation.repositoryId).copy();
            next.loading = false;
            next.message = message;
        }
        if (state.equals("ok") && operation.type.equals("COMMIT") && next.tree != null
                && !next.tree.headOid.isEmpty()) {
            RunRecord run = DevtoolDatabase.get(app).findRun(operation.runId);
            if (run != null) {
                run.commitHash = next.tree.headOid;
                DevtoolDatabase.get(app).upsertRun(run);
            }
        }
        synchronized (LOCK) {
            SNAPSHOTS.put(operation.repositoryId, next);
            ACTIVE.remove(operation.repositoryId);
        }
        broadcast(app, operation.repositoryId, message);
        app.sendBroadcast(new Intent(RepositoryStore.ACTION_UPDATED).setPackage(app.getPackageName()));
        new RepositoryNotificationController(app).show(queued.repository, operation);
        scheduleNext(app, operation.repositoryId);
    }

    private static void completeCancelled(Context app, QueuedOperation queued, String reason) {
        RepositoryOperationRecord operation = queued.record;
        long now = System.currentTimeMillis();
        operation.state = "cancelled";
        operation.finishedAt = now;
        operation.exitCode = 130;
        operation.cancellable = false;
        operation.summary = reason;
        DevtoolDatabase database = DevtoolDatabase.get(app);
        database.upsertRepositoryOperation(operation);
        database.addRepositoryEvent(operation.repositoryId, operation.runId,
                "GIT_" + operation.type + "_CANCELLED", reason, now);
        DebugRecorder.warning(app, "repo", "REPO_OPERATION_CANCELLED", "RepositoryWorkspaceStore",
                operation.runId, operation.repositoryId, operation.operationId, "", reason,
                DebugRecorder.details("operation", operation.type));
        finishSyntheticRun(app, operation, queued.repository, "cancelled", reason, "");
        broadcast(app, operation.repositoryId, reason);
        new RepositoryNotificationController(app).show(queued.repository, operation);
    }


    private static boolean activeInMemory(String repositoryId, String operationId) {
        synchronized (LOCK) {
            ActiveOperation active = ACTIVE.get(clean(repositoryId));
            return active != null && active.queued.record.operationId.equals(clean(operationId));
        }
    }

    private static Request requestFromRecord(RepositoryOperationRecord operation) {
        if (operation == null) return null;
        Operation type;
        try {
            type = Operation.valueOf(clean(operation.type).toUpperCase(java.util.Locale.ROOT));
        } catch (RuntimeException ignored) {
            return null;
        }
        switch (type) {
            case STAGE:
            case UNSTAGE:
            case DISCARD_WORKTREE:
                return operation.target.isEmpty() ? null : Request.paths(type, Collections.singletonList(operation.target));
            case STAGE_ALL:
            case UNSTAGE_ALL:
            case FETCH:
            case PULL_FF:
            case PUSH:
            case HARD_RESET:
                return Request.simple(type);
            case CHECKOUT:
                return operation.target.isEmpty() ? null : Request.branch(type, operation.target, false);
            case CREATE_BRANCH:
            case POP_STASH:
                return operation.target.isEmpty() ? null : Request.value(type, operation.target);
            case COMMIT:
            case STASH:
                return null;
            default:
                return null;
        }
    }

    private static RootCommandBroker.CommandResult perform(RootCommandBroker broker,
                                                            RepositoryRecord repository,
                                                            Request request) {
        switch (request.operation) {
            case STAGE: return broker.stage(repository.location(), request.paths);
            case UNSTAGE: return broker.unstage(repository.location(), request.paths);
            case DISCARD_WORKTREE: return broker.discardWorktree(repository.location(), request.paths);
            case STAGE_ALL: return broker.stageAll(repository.location());
            case UNSTAGE_ALL: return broker.unstageAll(repository.location());
            case COMMIT: return broker.commit(repository.location(), request.value);
            case FETCH: return broker.fetchAll(repository.location());
            case PULL_FF: return broker.pullFastForward(repository.location());
            case PUSH: return broker.push(repository.location());
            case CHECKOUT: return broker.checkoutBranch(repository.location(), request.value, request.flag);
            case CREATE_BRANCH: return broker.createBranch(repository.location(), request.value);
            case STASH: return broker.stashIncludingUntracked(repository.location(), request.value);
            case POP_STASH: return broker.popStash(repository.location(), request.value);
            case HARD_RESET: return broker.hardResetTracked(repository.location());
            default: return RootCommandBroker.CommandResult.error("Unsupported operation");
        }
    }

    private static Snapshot load(Context context, RepositoryRecord repository) {
        Snapshot next = new Snapshot();
        RootCommandBroker broker = new RootCommandBroker(context);
        next.tree = broker.workingTree(repository.location());
        next.commits = broker.commits(repository.location(), 100);
        next.branches = broker.branches(repository.location());
        next.stashes = broker.stashes(repository.location());
        next.syncPreview = broker.syncPreview(repository.location(), 20);
        next.remoteDiagnostics = broker.remoteDiagnostics(repository.location());
        next.operations = DevtoolDatabase.get(context).repositoryOperations(repository.repositoryId, 100);
        next.loading = false;
        next.updatedAt = System.currentTimeMillis();
        if (!next.tree.success()) next.message = next.tree.error;
        updateRepository(context, repository, next.tree);
        return next;
    }

    private static void updateRepository(Context context, RepositoryRecord source, GitWorkingTree tree) {
        if (tree == null || !tree.success()) return;
        RepositoryRecord repository = DevtoolDatabase.get(context).repository(source.repositoryId);
        if (repository == null) repository = source;
        repository.branch = tree.detached ? "detached" : tree.branch;
        repository.upstream = tree.upstream;
        repository.aheadCount = tree.ahead;
        repository.behindCount = tree.behind;
        repository.dirty = tree.dirty();
        repository.status = tree.dirty() ? "modified" : "clean";
        repository.lastScannedAt = System.currentTimeMillis();
        repository.lastError = "";
        DevtoolDatabase.get(context).upsertRepository(repository);
    }

    private static void createSyntheticRun(Context app, RepositoryRecord repository,
                                           RepositoryOperationRecord operation, Request request) {
        RunRecord run = new RunRecord();
        run.runId = operation.runId;
        run.title = "Git " + request.operation.displayName;
        run.status = "waiting";
        run.phase = "Git queue";
        run.detail = operation.summary;
        run.repo = repository.canonicalPath;
        run.repositoryId = repository.repositoryId;
        run.projectType = "git";
        run.executionEnvironment = repository.environment;
        run.workdir = repository.canonicalPath;
        run.target = request.target();
        run.command = request.commandDescription();
        run.launchMode = "root-broker";
        run.startedAtMs = operation.queuedAt;
        run.updatedAtMs = operation.queuedAt;
        run.lastProgressAtMs = operation.queuedAt;
        DevtoolDatabase.get(app).upsertRun(run);
        DevtoolDatabase.get(app).addLogEntry(run.runId, operation.queuedAt, "info", "git",
                "android_app", run.phase, "event", operation.summary, request.commandDescription());
        notifyRuns(app, run.runId);
    }

    private static void updateSyntheticRun(Context app, RepositoryRecord repository,
                                           RepositoryOperationRecord operation, String status,
                                           String message, String raw, String severity) {
        RunRecord run = DevtoolDatabase.get(app).findRun(operation.runId);
        if (run == null) run = new RunRecord();
        run.runId = operation.runId;
        run.title = "Git " + operation.type.replace('_', ' ').toLowerCase(java.util.Locale.ROOT);
        run.status = status;
        run.phase = "Git";
        run.detail = message;
        run.summary = message;
        run.repo = repository.canonicalPath;
        run.repositoryId = repository.repositoryId;
        run.projectType = "git";
        run.executionEnvironment = repository.environment;
        run.workdir = repository.canonicalPath;
        run.target = operation.target;
        run.updatedAtMs = System.currentTimeMillis();
        run.lastProgressAtMs = run.updatedAtMs;
        run.durationMs = Math.max(0L, run.updatedAtMs - run.startedAtMs);
        DevtoolDatabase.get(app).upsertRun(run);
        DevtoolDatabase.get(app).addLogEntry(run.runId, run.updatedAtMs, severity, "git",
                "android_app", run.phase, "event", message, raw);
        notifyRuns(app, run.runId);
    }

    private static void finishSyntheticRun(Context app, RepositoryOperationRecord operation,
                                           RepositoryRecord repository, String state,
                                           String message, String raw) {
        RunRecord run = DevtoolDatabase.get(app).findRun(operation.runId);
        if (run == null) run = new RunRecord();
        run.runId = operation.runId;
        run.title = "Git " + operation.type.replace('_', ' ').toLowerCase(java.util.Locale.ROOT);
        run.status = state.equals("ok") ? "ok" : state.equals("cancelled") ? "cancelled"
                : state.equals("interrupted") ? "interrupted" : "failed";
        run.phase = "Git";
        run.detail = message;
        run.summary = message;
        if (repository != null) {
            run.repo = repository.canonicalPath;
            run.repositoryId = repository.repositoryId;
            run.executionEnvironment = repository.environment;
            run.workdir = repository.canonicalPath;
        } else {
            run.repositoryId = operation.repositoryId;
        }
        run.projectType = "git";
        run.target = operation.target;
        run.exitCode = operation.exitCode;
        run.errors = run.status.equals("failed") ? 1 : 0;
        run.updatedAtMs = operation.finishedAt > 0 ? operation.finishedAt : System.currentTimeMillis();
        run.lastProgressAtMs = run.updatedAtMs;
        run.durationMs = Math.max(0L, run.updatedAtMs - run.startedAtMs);
        run.indeterminate = false;
        run.progress = run.status.equals("ok") ? 100 : run.progress;
        run.stderr = run.status.equals("failed") ? raw : "";
        run.stdout = run.status.equals("ok") ? raw : "";
        DevtoolDatabase.get(app).upsertRun(run);
        String severity = run.status.equals("ok") ? "success"
                : run.status.equals("failed") ? "error" : "warning";
        DevtoolDatabase.get(app).addLogEntry(run.runId, run.updatedAtMs, severity, "git",
                "tool_output", run.phase, run.status.equals("failed") ? "stderr" : "event",
                message, raw);
        notifyRuns(app, run.runId);
    }

    private static void notifyRuns(Context app, String runId) {
        app.sendBroadcast(new Intent(RunStore.ACTION_UPDATED).setPackage(app.getPackageName())
                .putExtra("run_id", runId));
    }

    private static void broadcast(Context context, String repositoryId, String message) {
        context.sendBroadcast(new Intent(ACTION_UPDATED).setPackage(context.getPackageName())
                .putExtra(EXTRA_REPOSITORY_ID, repositoryId)
                .putExtra(EXTRA_MESSAGE, clean(message)));
    }

    private static String redactOutput(String value) {
        String text = value == null ? "" : value;
        text = text.replaceAll("(?i)(https?://)[^\\s/@:]+:[^\\s/@]+@", "$1***@");
        text = text.replaceAll("(?i)(authorization:)[^\r\n]+", "$1 ***");
        if (text.length() > 512_000) text = text.substring(text.length() - 512_000);
        return text;
    }

    private static String safeDetail(String value) {
        String text = clean(value);
        text = text.replaceAll("(?i)(https?://)[^\\s/@:]+:[^\\s/@]+@", "$1***@");
        text = text.replaceAll("(?i)(authorization:)[^\\r\\n]+", "$1 ***");
        return compact(text, "");
    }

    private static String compact(String value, String fallback) {
        String text = clean(value).replace('\n', ' ').replace('\r', ' ')
                .replaceAll("\\s+", " ");
        if (text.isEmpty()) text = fallback;
        return text.length() > 350 ? text.substring(0, 347) + "…" : text;
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }

    private static Snapshot snapshotLocked(String repositoryId) {
        Snapshot value = SNAPSHOTS.get(clean(repositoryId));
        return value == null ? new Snapshot() : value;
    }

    static final class Snapshot {
        GitWorkingTree tree = new GitWorkingTree();
        List<GitCommitRecord> commits = new ArrayList<>();
        List<GitBranchRecord> branches = new ArrayList<>();
        List<GitStashRecord> stashes = new ArrayList<>();
        GitSyncPreview syncPreview = new GitSyncPreview();
        RemoteDiagnostics remoteDiagnostics = new RemoteDiagnostics();
        List<RepositoryOperationRecord> operations = new ArrayList<>();
        boolean loading;
        long updatedAt;
        String message = "";

        Snapshot copy() {
            Snapshot value = new Snapshot();
            value.tree = tree;
            value.commits = commits;
            value.branches = branches;
            value.stashes = stashes;
            value.syncPreview = syncPreview;
            value.remoteDiagnostics = remoteDiagnostics;
            value.operations = operations;
            value.loading = loading;
            value.updatedAt = updatedAt;
            value.message = message;
            return value;
        }
    }

    private static final class QueuedOperation {
        final RepositoryRecord repository;
        final Request request;
        final RepositoryOperationRecord record;

        QueuedOperation(RepositoryRecord repository, Request request, RepositoryOperationRecord record) {
            this.repository = repository;
            this.request = request;
            this.record = record;
        }
    }

    private static final class ActiveOperation {
        final QueuedOperation queued;
        final RootCommandBroker.CancellationToken token;

        ActiveOperation(QueuedOperation queued, RootCommandBroker.CancellationToken token) {
            this.queued = queued;
            this.token = token;
        }
    }

    private static final class Request {
        final Operation operation;
        final List<String> paths;
        final String value;
        final boolean flag;

        private Request(Operation operation, List<String> paths, String value, boolean flag) {
            this.operation = operation;
            this.paths = paths == null ? Collections.emptyList() : paths;
            this.value = clean(value);
            this.flag = flag;
        }

        static Request simple(Operation operation) {
            return new Request(operation, Collections.emptyList(), "", false);
        }

        static Request paths(Operation operation, List<String> paths) {
            return new Request(operation, paths, "", false);
        }

        static Request value(Operation operation, String value) {
            return new Request(operation, Collections.emptyList(), value, false);
        }

        static Request branch(Operation operation, String value, boolean remote) {
            return new Request(operation, Collections.emptyList(), value, remote);
        }

        String target() {
            if (!paths.isEmpty()) return paths.get(0);
            if (operation == Operation.COMMIT || operation == Operation.STASH) return "";
            return value;
        }

        String commandDescription() {
            switch (operation) {
                case STAGE: return "git add -- " + target();
                case UNSTAGE: return "git restore --staged -- " + target();
                case DISCARD_WORKTREE: return "git restore --worktree -- " + target();
                case STAGE_ALL: return "git add -A";
                case UNSTAGE_ALL: return "git restore --staged :/";
                case COMMIT: return "git commit";
                case FETCH: return "git fetch --all --prune --tags";
                case PULL_FF: return "git pull --ff-only";
                case PUSH: return "git push";
                case CHECKOUT: return "git switch " + target();
                case CREATE_BRANCH: return "git switch -c " + target();
                case STASH: return "git stash push --include-untracked";
                case POP_STASH: return "git stash pop " + target();
                case HARD_RESET: return "git reset --hard HEAD";
                default: return "git";
            }
        }
    }

    private enum Operation {
        STAGE("stage", "Queued file stage", "Staging file…", "File staged", "Unable to stage file", "File stage cancelled"),
        UNSTAGE("unstage", "Queued file unstage", "Unstaging file…", "File unstaged", "Unable to unstage file", "File unstage cancelled"),
        DISCARD_WORKTREE("discard", "Queued working-tree discard", "Discarding working-tree changes…", "Working-tree changes discarded", "Unable to discard changes", "Discard cancelled"),
        STAGE_ALL("stage all", "Queued stage all", "Staging all changes…", "All changes staged", "Unable to stage all changes", "Stage all cancelled"),
        UNSTAGE_ALL("unstage all", "Queued unstage all", "Unstaging all changes…", "All changes unstaged", "Unable to unstage all changes", "Unstage all cancelled"),
        COMMIT("commit", "Queued commit", "Creating commit…", "Commit created", "Unable to create commit", "Commit cancelled"),
        FETCH("fetch", "Queued fetch", "Fetching remotes…", "Fetch completed", "Unable to fetch", "Fetch cancelled"),
        PULL_FF("pull", "Queued fast-forward pull", "Pulling fast-forward only…", "Pull completed", "Unable to pull", "Pull cancelled"),
        PUSH("push", "Queued push", "Pushing branch…", "Push completed", "Unable to push", "Push cancelled"),
        CHECKOUT("checkout", "Queued branch checkout", "Switching branch…", "Branch switched", "Unable to switch branch", "Checkout cancelled"),
        CREATE_BRANCH("create branch", "Queued branch creation", "Creating branch…", "Branch created", "Unable to create branch", "Branch creation cancelled"),
        STASH("stash", "Queued stash", "Stashing changes including untracked files…", "Changes stashed", "Unable to stash changes", "Stash cancelled"),
        POP_STASH("pop stash", "Queued stash pop", "Applying and dropping stash…", "Stash popped", "Unable to pop stash", "Stash pop cancelled"),
        HARD_RESET("hard reset", "Queued tracked-file hard reset", "Resetting tracked files to HEAD…", "Tracked changes discarded", "Unable to reset tracked changes", "Hard reset cancelled");

        final String displayName;
        final String queuedLabel;
        final String progressLabel;
        final String successLabel;
        final String failureLabel;
        final String cancelledLabel;

        Operation(String displayName, String queuedLabel, String progressLabel, String successLabel,
                  String failureLabel, String cancelledLabel) {
            this.displayName = displayName;
            this.queuedLabel = queuedLabel;
            this.progressLabel = progressLabel;
            this.successLabel = successLabel;
            this.failureLabel = failureLabel;
            this.cancelledLabel = cancelledLabel;
        }
    }
}
