package io.github.mikeceead.devtoolhud;

final class RepositoryOperationRecord {
    String operationId = "";
    String repositoryId = "";
    String runId = "";
    String type = "";
    String target = "";
    String state = "queued";
    String progressLabel = "";
    String summary = "";
    long queuedAt;
    long queueOrder;
    long startedAt;
    long finishedAt;
    int exitCode = -1;
    boolean cancellable = true;

    boolean active() { return state.equals("queued") || state.equals("running") || state.equals("orphaned"); }
    boolean terminal() { return !active(); }

    long elapsedAt(long now) {
        long start = startedAt > 0 ? startedAt : queuedAt;
        long end = finishedAt > 0 ? finishedAt : now;
        return Math.max(0L, end - start);
    }
}
