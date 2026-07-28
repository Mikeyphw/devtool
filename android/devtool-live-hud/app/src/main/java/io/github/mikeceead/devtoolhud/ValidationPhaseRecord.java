package io.github.mikeceead.devtoolhud;

import java.util.ArrayList;
import java.util.List;

final class ValidationPhaseRecord {
    String sessionId = "";
    String key = "";
    String name = "";
    String label = "";
    String status = "pending";
    String lastVerdict = "";
    String currentAttemptId = "";
    String failureSummary = "";
    String logPath = "";
    String invalidatedReason = "";
    String inputDomainsJson = "[]";
    String dependenciesJson = "[]";
    String progressDetail = "";
    String progressUnit = "";
    String progressUpdatedAt = "";
    String workspace = "";
    String commandJson = "[]";
    String issuesJson = "[]";
    String classificationEvidenceJson = "[]";
    String supervisorEventsJson = "[]";
    String inputSnapshotBeforeJson = "{}";
    String inputSnapshotAfterJson = "{}";
    int index;
    int total;
    int attempts;
    int progress;
    int progressCompleted = -1;
    int progressTotal = -1;
    int signal;
    long durationMs;
    boolean required = true;
    boolean supportsProgress;
    final List<ValidationAttemptRecord> attemptHistory = new ArrayList<>();

    boolean passed() { return status.equals("passed") || status.equals("not-applicable") || status.equals("skipped"); }
    boolean running() { return status.equals("running") || status.equals("pausing"); }
    boolean failed() { return status.equals("failed"); }
    boolean runnable() { return status.equals("pending") || status.equals("interrupted") || status.equals("stale"); }
}
