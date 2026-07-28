package io.github.mikeceead.devtoolhud;

final class ValidationAttemptRecord {
    String attemptId = "";
    String phaseKey = "";
    String verdict = "";
    String failureKind = "";
    String category = "";
    String outputTail = "";
    String logPath = "";
    String startedAt = "";
    String finishedAt = "";
    String recommendedProfile = "";
    String argvJson = "[]";
    String issuesJson = "[]";
    String inputSnapshotBeforeJson = "{}";
    String inputSnapshotAfterJson = "{}";
    String classificationEvidenceJson = "[]";
    String supervisorEventsJson = "[]";
    String resourceProfile = "";
    long sessionRevision;
    int returnCode = -1;
    int signal;
    long durationMs;
    boolean retryable;
    boolean automaticRetry;
}
