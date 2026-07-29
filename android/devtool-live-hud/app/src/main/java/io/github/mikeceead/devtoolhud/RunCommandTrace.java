package io.github.mikeceead.devtoolhud;

import android.content.Context;

final class RunCommandTrace {
    static final String UI_RUN_REQUESTED = "UI_RUN_REQUESTED";
    static final String COMMAND_PLAN_BUILT = "COMMAND_PLAN_BUILT";
    static final String COMMAND_ENVELOPE_WRITTEN = "COMMAND_ENVELOPE_WRITTEN";
    static final String TERMUX_INTENT_BUILT = "TERMUX_INTENT_BUILT";
    static final String TERMUX_INTENT_SENT = "TERMUX_INTENT_SENT";
    static final String TERMUX_RUNCOMMAND_ACCEPTED = "TERMUX_RUNCOMMAND_ACCEPTED";
    static final String RUNSTORE_CREATED = "RUNSTORE_CREATED";
    static final String TERMUX_SHELL_STARTED = "TERMUX_SHELL_STARTED";
    static final String TERMUX_WRAPPER_STARTED = "TERMUX_WRAPPER_STARTED";
    static final String SHELL_RESOLVED = "SHELL_RESOLVED";
    static final String TOKEN_MARKERS_OK = "TOKEN_MARKERS_OK";
    static final String TOKEN_MARKERS_MISSING = "TOKEN_MARKERS_MISSING";
    static final String WORKDIR_CHECKED = "WORKDIR_CHECKED";
    static final String WORKDIR_MISSING = "WORKDIR_MISSING";
    static final String ENV_READY = "ENV_READY";
    static final String COMMAND_FOUND = "COMMAND_FOUND";
    static final String COMMAND_LOOKUP_SKIPPED = "COMMAND_LOOKUP_SKIPPED";
    static final String COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND";
    static final String EXEC_STARTING = "EXEC_STARTING";
    static final String EXIT_CODE_RECORDED = "EXIT_CODE_RECORDED";
    static final String FIRST_OUTPUT = "FIRST_OUTPUT";
    static final String FIRST_PROGRESS_EVENT_RECEIVED = "FIRST_PROGRESS_EVENT_RECEIVED";
    static final String RUN_ACTIVE = "RUN_ACTIVE";
    static final String RUN_PROGRESS_WATCHDOG_WAITING = "RUN_PROGRESS_WATCHDOG_WAITING";
    static final String TERMUX_RESULT_RECEIVED = "TERMUX_RESULT_RECEIVED";
    static final String RUN_COMPLETED = "RUN_COMPLETED";
    static final String RUN_FAILED = "RUN_FAILED";
    static final String BRIDGE_ROUNDTRIP_CALLBACK_RECEIVED = "BRIDGE_ROUNDTRIP_CALLBACK_RECEIVED";
    static final String BRIDGE_ROUNDTRIP_RESULT_RECEIVED = "BRIDGE_ROUNDTRIP_RESULT_RECEIVED";
    static final String BRIDGE_ROUNDTRIP_FAILED = "BRIDGE_ROUNDTRIP_FAILED";
    static final String ROOT_BRIDGE_DISPATCHED = "ROOT_BRIDGE_DISPATCHED";
    static final String ROOT_BRIDGE_PROCESS_STARTED = "ROOT_BRIDGE_PROCESS_STARTED";

    private RunCommandTrace() {}

    static void record(Context context, String checkpoint, String surface, String runId,
                       String message, String detailJson) {
        if (context == null) return;
        String cleanCheckpoint = normalizeCheckpoint(checkpoint);
        String cleanMessage = clean(message).isEmpty() ? label(cleanCheckpoint) : clean(message);
        String cleanRunId = clean(runId);
        if (!cleanRunId.isEmpty()
                && RunStore.traceCheckpoint(context, cleanRunId, cleanCheckpoint, cleanMessage, detailJson)) {
            return;
        }
        DebugRecorder.info(context, "run_trace", cleanCheckpoint, surface,
                cleanRunId, "", "", "", cleanMessage, enrich(cleanCheckpoint, detailJson));
    }

    static String enrich(String checkpoint, String detailJson) {
        return DebugRecorder.details(
                "checkpoint", normalizeCheckpoint(checkpoint),
                "checkpoint_order", order(checkpoint),
                "detail", clean(detailJson).isEmpty() ? "{}" : detailJson);
    }

    static String fromBridgeIntent(android.content.Intent intent) {
        if (intent == null) return "";
        return normalizeCheckpoint(intent.getStringExtra("trace_checkpoint"));
    }

    static String bridgeMessage(android.content.Intent intent) {
        if (intent == null) return "";
        return clean(intent.getStringExtra("trace_message"));
    }

    static String label(String checkpoint) {
        String clean = normalizeCheckpoint(checkpoint).replace('_', ' ').toLowerCase(java.util.Locale.ROOT);
        if (clean.isEmpty()) return "Run trace checkpoint";
        return clean.substring(0, 1).toUpperCase(java.util.Locale.ROOT) + clean.substring(1);
    }

    static int order(String checkpoint) {
        switch (normalizeCheckpoint(checkpoint)) {
            case UI_RUN_REQUESTED: return 10;
            case COMMAND_PLAN_BUILT: return 20;
            case COMMAND_ENVELOPE_WRITTEN: return 30;
            case TERMUX_INTENT_BUILT: return 40;
            case TERMUX_INTENT_SENT: return 50;
            case TERMUX_RUNCOMMAND_ACCEPTED: return 60;
            case RUNSTORE_CREATED: return 70;
            case TERMUX_SHELL_STARTED: return 80;
            case TERMUX_WRAPPER_STARTED: return 82;
            case SHELL_RESOLVED: return 84;
            case TOKEN_MARKERS_OK: return 86;
            case TOKEN_MARKERS_MISSING: return 87;
            case WORKDIR_CHECKED: return 88;
            case WORKDIR_MISSING: return 89;
            case ENV_READY: return 90;
            case COMMAND_FOUND: return 92;
            case COMMAND_LOOKUP_SKIPPED: return 93;
            case COMMAND_NOT_FOUND: return 94;
            case EXEC_STARTING: return 96;
            case ROOT_BRIDGE_DISPATCHED: return 97;
            case ROOT_BRIDGE_PROCESS_STARTED: return 98;
            case FIRST_OUTPUT: return 99;
            case FIRST_PROGRESS_EVENT_RECEIVED: return 100;
            case BRIDGE_ROUNDTRIP_CALLBACK_RECEIVED: return 102;
            case RUN_ACTIVE: return 110;
            case RUN_PROGRESS_WATCHDOG_WAITING: return 120;
            case EXIT_CODE_RECORDED: return 130;
            case TERMUX_RESULT_RECEIVED: return 140;
            case BRIDGE_ROUNDTRIP_RESULT_RECEIVED: return 142;
            case BRIDGE_ROUNDTRIP_FAILED: return 143;
            case RUN_COMPLETED: return 150;
            case RUN_FAILED: return 160;
            default: return 999;
        }
    }

    static String normalizeCheckpoint(String value) {
        String clean = clean(value).replaceAll("[^A-Za-z0-9_:-]", "_").toUpperCase(java.util.Locale.ROOT);
        return clean.isEmpty() ? "RUN_TRACE_CHECKPOINT" : clean;
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
