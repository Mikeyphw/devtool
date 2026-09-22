package io.github.mikeceead.devtoolhud;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/** Canonical EXO execution graph transported by the Python bridge. */
final class ExecutionGraphState {
    static final class Node {
        final String id;
        final String parentId;
        final String label;
        final String kind;
        final String status;
        final int percent;
        final String detail;
        final List<String> dependsOn;
        final List<String> blockedBy;
        final List<String> diagnosticIds;
        final List<String> processIds;
        final List<String> artifactIds;
        final String transitionOrigin;
        final String transitionReason;

        Node(JSONObject value) {
            id = value.optString("node_id", "");
            parentId = value.optString("parent_id", "");
            label = value.optString("label", kind(value));
            kind = kind(value);
            status = normalizeStatus(value.optString("status", "planned"));
            JSONObject progress = value.optJSONObject("progress");
            percent = clamp(progress == null ? -1 : progress.optDouble("percent", -1.0));
            detail = progress == null ? "" : progress.optString("detail", "");
            dependsOn = strings(value.optJSONArray("depends_on"));
            blockedBy = strings(value.optJSONArray("blocked_by"));
            diagnosticIds = strings(value.optJSONArray("diagnostic_ids"));
            processIds = strings(value.optJSONArray("process_ids"));
            artifactIds = strings(value.optJSONArray("artifact_ids"));
            transitionOrigin = value.optString("transition_origin", "");
            transitionReason = value.optString("transition_reason", "");
        }

        private static String kind(JSONObject value) {
            String result = value.optString("kind", "operation");
            return result.isEmpty() ? "operation" : result;
        }
    }

    static final class Diagnostic {
        final String id;
        final String nodeId;
        final String processId;
        final String severity;
        final String code;
        final String message;
        final String failureKind;
        final String logPath;
        final List<String> evidence;

        Diagnostic(JSONObject value) {
            id = value.optString("diagnostic_id", "");
            nodeId = value.optString("node_id", "");
            processId = value.optString("process_id", "");
            severity = value.optString("severity", "info").toLowerCase(Locale.ROOT);
            code = value.optString("code", "");
            message = value.optString("message", "");
            failureKind = value.optString("failure_kind", "");
            logPath = value.optString("log_path", "");
            evidence = strings(value.optJSONArray("evidence"));
        }
    }

    final int schemaVersion;
    final String runId;
    final long sequence;
    final String eventId;
    final String status;
    final boolean graphSealed;
    final int measuredPercent;
    final int resolutionPercent;
    final int measurementCoveragePercent;
    final boolean determinate;
    final int totalOperations;
    final int succeededOperations;
    final int failedOperations;
    final int blockedOperations;
    final List<Node> nodes;
    final List<Diagnostic> diagnostics;
    final JSONObject raw;

    private ExecutionGraphState(JSONObject value) {
        raw = value == null ? new JSONObject() : value;
        schemaVersion = raw.optInt("schema_version", 0);
        runId = raw.optString("run_id", "");
        sequence = raw.optLong("last_sequence", -1L);
        eventId = raw.optString("last_event_id", "");
        status = normalizeStatus(raw.optString("status", "waiting"));
        graphSealed = raw.optBoolean("graph_sealed", false);

        JSONObject accounting = raw.optJSONObject("accounting");
        JSONObject leaf = accounting == null ? null : accounting.optJSONObject("leaf");
        JSONObject measured = accounting == null ? null : accounting.optJSONObject("leaf_measured_progress");
        measuredPercent = clamp(measured == null ? -1 : measured.optDouble("percent", -1.0));
        measurementCoveragePercent = clamp(measured == null ? -1 : measured.optDouble("coverage_percent", -1.0));
        determinate = measured != null && measured.optBoolean("determinate", false);
        resolutionPercent = clamp(accounting == null ? -1 : accounting.optDouble("leaf_resolution_percent", -1.0));
        totalOperations = count(leaf, "total");
        succeededOperations = count(leaf, "succeeded");
        failedOperations = count(leaf, "failed");
        blockedOperations = count(leaf, "blocked");

        nodes = Collections.unmodifiableList(parseNodes(raw.optJSONArray("nodes")));
        diagnostics = Collections.unmodifiableList(parseDiagnostics(raw.optJSONArray("diagnostics")));
    }

    static ExecutionGraphState parse(String json) {
        if (json == null || json.trim().isEmpty()) return empty();
        try {
            return new ExecutionGraphState(new JSONObject(json));
        } catch (Exception ignored) {
            return empty();
        }
    }

    static ExecutionGraphState empty() { return new ExecutionGraphState(new JSONObject()); }

    boolean available() { return !runId.isEmpty() && (schemaVersion > 0 || !nodes.isEmpty()); }

    Node activeNode() {
        Node terminalFailure = null;
        for (Node node : nodes) {
            if (node.status.equals("running")) return node;
            if (node.status.equals("failed") || node.status.equals("blocked") || node.status.equals("cancelled")) {
                terminalFailure = node;
            }
        }
        return terminalFailure;
    }

    List<HudUpdate.Step> steps() {
        List<HudUpdate.Step> result = new ArrayList<>();
        Set<String> parentIds = new HashSet<>();
        for (Node node : nodes) if (!node.parentId.isEmpty()) parentIds.add(node.parentId);
        for (Node node : nodes) {
            // Hide only a synthetic top-level aggregate that owns children.
            // Independent top-level work nodes must remain visible.
            if (node.parentId.isEmpty() && parentIds.contains(node.id) && nodes.size() > 1) continue;
            result.add(new HudUpdate.Step(
                    node.id.isEmpty() ? "operation" : node.id,
                    node.label.isEmpty() ? node.kind : node.label,
                    uiStatus(node.status),
                    node.percent,
                    node.kind,
                    node.parentId,
                    node.dependsOn,
                    node.blockedBy,
                    node.detail,
                    node.transitionOrigin,
                    node.transitionReason));
        }
        return result;
    }

    String primaryFailureMessage() {
        for (Diagnostic item : diagnostics) {
            if (item.severity.equals("error")) return item.message;
        }
        return "";
    }

    private static int count(JSONObject value, String key) {
        return value == null ? 0 : Math.max(0, value.optInt(key, 0));
    }

    private static int clamp(double value) {
        if (value < 0) return -1;
        return Math.max(0, Math.min(100, (int) Math.round(value)));
    }

    private static List<Node> parseNodes(JSONArray values) {
        List<Node> result = new ArrayList<>();
        if (values == null) return result;
        for (int i = 0; i < values.length(); i++) {
            JSONObject item = values.optJSONObject(i);
            if (item != null) result.add(new Node(item));
        }
        return result;
    }

    private static List<Diagnostic> parseDiagnostics(JSONArray values) {
        List<Diagnostic> result = new ArrayList<>();
        if (values == null) return result;
        for (int i = 0; i < values.length(); i++) {
            JSONObject item = values.optJSONObject(i);
            if (item != null) result.add(new Diagnostic(item));
        }
        return result;
    }

    private static List<String> strings(JSONArray values) {
        List<String> result = new ArrayList<>();
        if (values == null) return result;
        for (int i = 0; i < values.length(); i++) {
            String item = values.optString(i, "");
            if (!item.isEmpty()) result.add(item);
        }
        return Collections.unmodifiableList(result);
    }

    static String normalizeStatus(String value) {
        String status = value == null ? "" : value.trim().toLowerCase(Locale.ROOT);
        if (status.equals("succeeded") || status.equals("success") || status.equals("passed")) return "ok";
        if (status.equals("cancelled") || status.equals("canceled")) return "cancelled";
        if (status.equals("planned")) return "waiting";
        return status.isEmpty() ? "waiting" : status;
    }

    private static String uiStatus(String value) {
        return normalizeStatus(value);
    }
}
