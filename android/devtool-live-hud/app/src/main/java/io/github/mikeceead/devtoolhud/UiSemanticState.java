package io.github.mikeceead.devtoolhud;

import android.content.Intent;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

/**
 * Parsed UI Core contract transported by the Termux bridge.
 *
 * Android remains a native renderer: this class consumes semantic state,
 * actions, navigation, progress and diagnostics but never interprets Devtool
 * logs or reconstructs policy locally. Missing/older fields intentionally fall
 * back to the legacy HudUpdate contract.
 */
final class UiSemanticState {
    static final int SCHEMA_VERSION = 1;

    final int schemaVersion;
    final JSONObject snapshot;
    final JSONObject operation;
    final JSONObject presentation;
    final JSONObject capabilities;
    final JSONObject responsive;
    final JSONObject progressSummary;
    final JSONArray progressTree;
    final JSONObject diagnosticSummary;
    final JSONArray diagnostics;
    final List<Action> actions;
    final List<Navigation> navigation;
    final JSONObject confirmationPolicy;
    final JSONObject notificationPolicy;
    final List<SemanticNotification> notifications;

    private UiSemanticState(
            int schemaVersion,
            JSONObject snapshot,
            JSONObject operation,
            JSONObject presentation,
            JSONObject capabilities,
            JSONObject responsive,
            JSONObject progressSummary,
            JSONArray progressTree,
            JSONObject diagnosticSummary,
            JSONArray diagnostics,
            List<Action> actions,
            List<Navigation> navigation,
            JSONObject confirmationPolicy,
            JSONObject notificationPolicy,
            List<SemanticNotification> notifications) {
        this.schemaVersion = schemaVersion;
        this.snapshot = snapshot;
        this.operation = operation;
        this.presentation = presentation;
        this.capabilities = capabilities;
        this.responsive = responsive;
        this.progressSummary = progressSummary;
        this.progressTree = progressTree;
        this.diagnosticSummary = diagnosticSummary;
        this.diagnostics = diagnostics;
        this.actions = Collections.unmodifiableList(actions);
        this.navigation = Collections.unmodifiableList(navigation);
        this.confirmationPolicy = confirmationPolicy;
        this.notificationPolicy = notificationPolicy;
        this.notifications = Collections.unmodifiableList(notifications);
    }

    static UiSemanticState empty() {
        return new UiSemanticState(
                SCHEMA_VERSION, new JSONObject(), new JSONObject(), new JSONObject(),
                new JSONObject(), new JSONObject(), new JSONObject(), new JSONArray(),
                new JSONObject(), new JSONArray(), new ArrayList<>(), new ArrayList<>(),
                new JSONObject(), new JSONObject(), new ArrayList<>());
    }

    static UiSemanticState fromIntent(Intent intent) {
        if (intent == null) return empty();
        JSONObject snapshot = object(intent.getStringExtra("ui_snapshot_json"));
        int version = intent.getIntExtra("ui_schema_version", snapshot.optInt("schema_version", SCHEMA_VERSION));
        JSONObject operation = preferObject(childObject(snapshot, "operation"), object(intent.getStringExtra("ui_operation_json")));
        JSONObject presentation = preferObject(childObject(snapshot, "presentation"), object(intent.getStringExtra("ui_presentation_json")));
        JSONObject capabilities = preferObject(childObject(snapshot, "capabilities"), object(intent.getStringExtra("ui_capabilities_json")));
        JSONObject responsive = preferObject(childObject(snapshot, "responsive"), object(intent.getStringExtra("ui_responsive_json")));
        JSONObject progressSummary = preferObject(childObject(snapshot, "progress_summary"), object(intent.getStringExtra("ui_progress_summary_json")));
        JSONArray progressTree = preferArray(childArray(snapshot, "progress_tree"), array(intent.getStringExtra("ui_progress_tree_json")));
        JSONObject diagnosticSummary = preferObject(childObject(snapshot, "diagnostic_summary"), object(intent.getStringExtra("ui_diagnostic_summary_json")));
        JSONArray diagnostics = preferArray(childArray(snapshot, "diagnostic_presentations"), array(intent.getStringExtra("ui_diagnostics_json")));
        JSONArray actionsJson = preferArray(childArray(snapshot, "actions"), array(intent.getStringExtra("ui_actions_json")));
        JSONArray navigationJson = preferArray(childArray(snapshot, "navigation"), array(intent.getStringExtra("ui_navigation_json")));
        JSONObject confirmationPolicy = preferObject(childObject(snapshot, "confirmation_policy"), object(intent.getStringExtra("ui_confirmation_policy_json")));
        JSONObject notificationPolicy = preferObject(childObject(snapshot, "notification_policy"), object(intent.getStringExtra("ui_notification_policy_json")));
        JSONArray notificationsJson = preferArray(childArray(snapshot, "notifications"), array(intent.getStringExtra("ui_notifications_json")));
        return new UiSemanticState(
                version, snapshot, operation, presentation, capabilities, responsive,
                progressSummary, progressTree, diagnosticSummary, diagnostics,
                parseActions(actionsJson), parseNavigation(navigationJson), confirmationPolicy,
                notificationPolicy, parseNotifications(notificationsJson));
    }

    boolean available() {
        return snapshot.length() > 0 || operation.length() > 0 || !actions.isEmpty()
                || progressSummary.length() > 0 || diagnostics.length() > 0;
    }

    String operationTitle(String fallback) { return text(operation, "title", fallback); }
    String operationDetail(String fallback) { return text(operation, "detail", fallback); }
    String operationStatus(String fallback) { return text(operation, "status", fallback).toLowerCase(Locale.ROOT); }
    String operationPhase(String fallback) { return text(operation, "phase", fallback); }

    int progressPercent(int fallback) {
        int value = progressSummary.optInt("percent", -1);
        if (value < 0) value = operation.optInt("percent", -1);
        if (value < 0) return fallback;
        return Math.max(0, Math.min(100, value));
    }

    String attentionLevel() {
        return notifications.isEmpty() ? "" : notifications.get(0).level;
    }

    boolean shouldNotify(boolean fallback) {
        return notifications.isEmpty() ? fallback : notifications.get(0).shouldEmit;
    }

    String notificationTitle(String fallback) {
        return notifications.isEmpty() ? fallback : clean(notifications.get(0).title, fallback);
    }

    String notificationBody(String fallback) {
        return notifications.isEmpty() ? fallback : clean(notifications.get(0).body, fallback);
    }


    List<HudUpdate.Step> progressSteps() {
        List<HudUpdate.Step> result = new ArrayList<>();
        appendProgressSteps(progressTree, result);
        return result;
    }

    private static void appendProgressSteps(JSONArray values, List<HudUpdate.Step> output) {
        for (int i = 0; i < values.length(); i++) {
            JSONObject item = values.optJSONObject(i);
            if (item == null) continue;
            String kind = text(item, "kind", "step");
            if (!kind.equals("operation") && !kind.equals("workflow") && !kind.equals("target")) {
                output.add(new HudUpdate.Step(
                        text(item, "id", "step"),
                        text(item, "label", kind),
                        text(item, "status", "pending"),
                        item.optInt("percent", item.optInt("progress", 0))));
            }
            JSONArray children = item.optJSONArray("children");
            if (children != null) appendProgressSteps(children, output);
        }
    }

    Action action(String id) {
        for (Action action : actions) if (action.id.equals(id)) return action;
        return null;
    }

    List<Diagnostic> diagnosticItems(int max) {
        List<Diagnostic> result = new ArrayList<>();
        int limit = Math.max(0, max);
        for (int i = 0; i < diagnostics.length() && (limit == 0 || result.size() < limit); i++) {
            JSONObject item = diagnostics.optJSONObject(i);
            if (item == null) continue;
            result.add(new Diagnostic(
                    text(item, "severity", "info"), text(item, "summary", text(item, "what_happened", "")),
                    text(item, "cause", text(item, "why", "")), text(item, "location", text(item, "where", "")),
                    text(item, "technical_details", "")));
        }
        return result;
    }

    private static List<Action> parseActions(JSONArray values) {
        List<Action> result = new ArrayList<>();
        for (int i = 0; i < values.length(); i++) {
            JSONObject item = values.optJSONObject(i);
            if (item == null) continue;
            String id = text(item, "id", "");
            if (id.isEmpty()) continue;
            result.add(new Action(
                    id,
                    text(item, "label", id),
                    text(item, "short_label", text(item, "label", id)),
                    text(item, "notification_label", text(item, "short_label", text(item, "label", id))),
                    text(item, "intent", ""),
                    item.optBoolean("enabled", true),
                    item.optBoolean("destructive", false),
                    item.optBoolean("requires_confirmation", false)));
        }
        return result;
    }

    private static List<Navigation> parseNavigation(JSONArray values) {
        List<Navigation> result = new ArrayList<>();
        for (int i = 0; i < values.length(); i++) {
            JSONObject item = values.optJSONObject(i);
            if (item == null) continue;
            String id = text(item, "id", "");
            String route = text(item, "route", "");
            if (id.isEmpty() || route.isEmpty()) continue;
            result.add(new Navigation(id, text(item, "label", id), route, text(item, "icon", "")));
        }
        return result;
    }

    private static List<SemanticNotification> parseNotifications(JSONArray values) {
        List<SemanticNotification> result = new ArrayList<>();
        for (int i = 0; i < values.length(); i++) {
            JSONObject item = values.optJSONObject(i);
            if (item == null) continue;
            result.add(new SemanticNotification(
                    text(item, "level", "info"), text(item, "title", "Devtool"),
                    text(item, "body", ""), item.optBoolean("should_emit", true),
                    item.optBoolean("bell", false)));
        }
        return result;
    }

    private static JSONObject object(String raw) {
        if (raw == null || raw.trim().isEmpty()) return new JSONObject();
        try { return new JSONObject(raw); } catch (JSONException ignored) { return new JSONObject(); }
    }

    private static JSONArray array(String raw) {
        if (raw == null || raw.trim().isEmpty()) return new JSONArray();
        try { return new JSONArray(raw); } catch (JSONException ignored) { return new JSONArray(); }
    }

    private static JSONObject childObject(JSONObject parent, String key) {
        JSONObject value = parent.optJSONObject(key);
        return value == null ? new JSONObject() : value;
    }

    private static JSONArray childArray(JSONObject parent, String key) {
        JSONArray value = parent.optJSONArray(key);
        return value == null ? new JSONArray() : value;
    }

    private static JSONObject preferObject(JSONObject primary, JSONObject fallback) {
        return primary.length() > 0 ? primary : fallback;
    }

    private static JSONArray preferArray(JSONArray primary, JSONArray fallback) {
        return primary.length() > 0 ? primary : fallback;
    }

    private static String text(JSONObject value, String key, String fallback) {
        return clean(value.optString(key, ""), fallback);
    }

    private static String clean(String value, String fallback) {
        String clean = value == null ? "" : value.trim();
        return clean.isEmpty() ? (fallback == null ? "" : fallback) : clean;
    }

    static final class Action {
        final String id, label, shortLabel, notificationLabel, intent;
        final boolean enabled, destructive, requiresConfirmation;
        Action(String id, String label, String shortLabel, String notificationLabel, String intent,
               boolean enabled, boolean destructive, boolean requiresConfirmation) {
            this.id = id; this.label = label; this.shortLabel = shortLabel;
            this.notificationLabel = notificationLabel; this.intent = intent;
            this.enabled = enabled; this.destructive = destructive; this.requiresConfirmation = requiresConfirmation;
        }
    }

    static final class Navigation {
        final String id, label, route, icon;
        Navigation(String id, String label, String route, String icon) {
            this.id = id; this.label = label; this.route = route; this.icon = icon;
        }
    }

    static final class Diagnostic {
        final String severity, summary, cause, location, technicalDetails;
        Diagnostic(String severity, String summary, String cause, String location, String technicalDetails) {
            this.severity = severity; this.summary = summary; this.cause = cause;
            this.location = location; this.technicalDetails = technicalDetails;
        }
    }

    static final class SemanticNotification {
        final String level, title, body;
        final boolean shouldEmit, bell;
        SemanticNotification(String level, String title, String body, boolean shouldEmit, boolean bell) {
            this.level = level; this.title = title; this.body = body;
            this.shouldEmit = shouldEmit; this.bell = bell;
        }
    }
}
