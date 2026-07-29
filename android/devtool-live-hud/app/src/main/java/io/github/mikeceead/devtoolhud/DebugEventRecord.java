package io.github.mikeceead.devtoolhud;

final class DebugEventRecord {
    long eventId;
    long timestampMs;
    String severity = "info";
    String category = "app";
    String eventType = "DEBUG_EVENT";
    String surface = "";
    String runId = "";
    String repositoryId = "";
    String operationId = "";
    String validationSessionId = "";
    String message = "";
    String detailJson = "{}";
    String fingerprint = "";
    String redactionState = "redacted";

    DebugEventRecord() {}

    boolean problem() {
        return severity.equals("warning") || severity.equals("error") || severity.equals("fatal");
    }
}
