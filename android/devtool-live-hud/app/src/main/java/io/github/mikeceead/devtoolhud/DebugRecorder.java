package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.Intent;

import org.json.JSONObject;

final class DebugRecorder {
    static final String ACTION_UPDATED = "io.github.mikeceead.devtoolhud.DEBUG_EVENTS_UPDATED";
    private static final String TAG = "DevtoolDebugRecorder";
    private static final int MAX_TEXT = 4096;

    private DebugRecorder() {}

    static void info(Context context, String category, String eventType, String surface,
                     String runId, String repositoryId, String operationId,
                     String validationSessionId, String message, String detailJson) {
        record(context, "info", category, eventType, surface, runId, repositoryId,
                operationId, validationSessionId, message, detailJson);
    }

    static void success(Context context, String category, String eventType, String surface,
                        String runId, String repositoryId, String operationId,
                        String validationSessionId, String message, String detailJson) {
        record(context, "success", category, eventType, surface, runId, repositoryId,
                operationId, validationSessionId, message, detailJson);
    }

    static void warning(Context context, String category, String eventType, String surface,
                        String runId, String repositoryId, String operationId,
                        String validationSessionId, String message, String detailJson) {
        record(context, "warning", category, eventType, surface, runId, repositoryId,
                operationId, validationSessionId, message, detailJson);
    }

    static void error(Context context, String category, String eventType, String surface,
                      String runId, String repositoryId, String operationId,
                      String validationSessionId, String message, String detailJson) {
        record(context, "error", category, eventType, surface, runId, repositoryId,
                operationId, validationSessionId, message, detailJson);
    }

    static void record(Context context, String severity, String category, String eventType, String surface,
                       String runId, String repositoryId, String operationId,
                       String validationSessionId, String message, String detailJson) {
        if (context == null) return;
        DebugEventRecord event = new DebugEventRecord();
        event.timestampMs = System.currentTimeMillis();
        event.severity = severity(severity);
        event.category = token(category, "app").toLowerCase(java.util.Locale.ROOT);
        event.eventType = token(eventType, "DEBUG_EVENT").toUpperCase(java.util.Locale.ROOT);
        event.surface = limit(token(surface, "unknown"));
        event.runId = limit(clean(runId));
        event.repositoryId = limit(clean(repositoryId));
        event.operationId = limit(clean(operationId));
        event.validationSessionId = limit(clean(validationSessionId));
        event.message = limit(redact(message));
        event.detailJson = normalizeJson(detailJson);
        event.fingerprint = DebugIssueClassifier.fingerprint(event.category, event.eventType,
                event.message, event.detailJson);
        event.redactionState = "redacted";
        try {
            event.eventId = DevtoolDatabase.get(context.getApplicationContext()).addDebugEvent(event);
            DebugNotificationController.maybePost(context.getApplicationContext(), event);
            context.getApplicationContext().sendBroadcast(new Intent(ACTION_UPDATED)
                    .setPackage(context.getPackageName())
                    .putExtra("fingerprint", event.fingerprint)
                    .putExtra("category", event.category));
        } catch (RuntimeException failure) {
            AndroidPrivacyLog.warn(TAG, "Unable to persist debug event " + event.eventType, failure);
        }
    }

    static String details(Object... pairs) {
        JSONObject value = new JSONObject();
        if (pairs == null) return "{}";
        for (int i = 0; i + 1 < pairs.length; i += 2) {
            String key = clean(String.valueOf(pairs[i]));
            if (key.isEmpty()) continue;
            Object raw = pairs[i + 1];
            try {
                if (raw == null) value.put(key, "");
                else if (raw instanceof Number || raw instanceof Boolean) value.put(key, raw);
                else value.put(key, redact(String.valueOf(raw)));
            } catch (Exception ignored) {
                // JSONObject only rejects invalid numeric values; omit those fields.
            }
        }
        return value.toString();
    }

    private static String normalizeJson(String value) {
        String clean = redact(value);
        if (clean.isEmpty()) return "{}";
        String limited = limit(clean);
        String trimmed = limited.trim();
        if (trimmed.startsWith("{") && trimmed.endsWith("}")) return trimmed;
        return details("detail", trimmed);
    }

    private static String severity(String value) {
        String clean = clean(value).toLowerCase(java.util.Locale.ROOT);
        if (clean.equals("success") || clean.equals("warning") || clean.equals("error") || clean.equals("fatal")) return clean;
        return "info";
    }

    private static String token(String value, String fallback) {
        String clean = clean(value).replaceAll("[^A-Za-z0-9_.:-]", "_");
        return clean.isEmpty() ? fallback : clean;
    }

    private static String redact(String value) { return SupportRedactor.redact(value == null ? "" : value); }

    private static String limit(String value) {
        String clean = value == null ? "" : value;
        return clean.length() <= MAX_TEXT ? clean : clean.substring(0, MAX_TEXT);
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
