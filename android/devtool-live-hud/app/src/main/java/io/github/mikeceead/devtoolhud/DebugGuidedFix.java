package io.github.mikeceead.devtoolhud;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Phase 44 guided fixes for Debug Workbench.
 *
 * Each card is deliberately non-destructive: it explains the likely fault,
 * points to the safest surface, and offers copyable evidence. Actual repair
 * buttons stay on their owner screens where confirmations already exist.
 */
final class DebugGuidedFix {
    static final String BUNDLE_KEY = "guided_fixes";
    private static final int MAX_FIXES = 10;
    private static final int MAX_EVENT_FIXES = 6;
    private static final int MAX_REPO_FIXES = 4;

    String issueKey = "generic_event";
    String severity = "warning";
    String title = "Review diagnostic evidence";
    String whatHappened = "Devtool recorded a diagnostic event that may need review.";
    String whatItMeans = "The event was retained by the debug recorder, but no specialized guided fix matched it.";
    String evidence = "Open recent debug events for the raw breadcrumb.";
    String nextAction = "Open Debug Workbench and copy the recent debug events if you need to share it.";
    String actionLabel = "Open Debug Workbench";
    String actionKind = "debug_workbench";
    String runId = "";
    String repositoryId = "";
    String validationSessionId = "";

    static List<DebugGuidedFix> from(Context context, DebugWorkbenchHealth.Snapshot snapshot) {
        Context app = context == null ? null : context.getApplicationContext();
        ArrayList<DebugGuidedFix> fixes = new ArrayList<>();
        HashSet<String> seen = new HashSet<>();
        if (snapshot == null) return fixes;

        addEnvironmentFixes(app, snapshot, fixes, seen);
        addRunFix(snapshot, fixes, seen);
        addValidationFix(snapshot, fixes, seen);
        addRepositoryFixes(app, snapshot, fixes, seen);
        addEventFixes(snapshot, fixes, seen);
        return limit(fixes, MAX_FIXES);
    }

    static JSONObject supportSummary(Context context) throws JSONException {
        DebugWorkbenchHealth.Snapshot snapshot = DebugWorkbenchHealth.assess(context);
        JSONArray values = new JSONArray();
        for (DebugGuidedFix fix : from(context, snapshot)) values.put(fix.toJson());
        JSONObject root = new JSONObject();
        root.put("status", values.length() == 0 ? "pass" : "warn");
        root.put("count", values.length());
        root.put("items", values);
        root.put("safety", "Guided fixes are explanatory/deep-link actions only; destructive Git operations are not run from the workbench.");
        return root;
    }

    JSONObject toJson() throws JSONException {
        JSONObject value = new JSONObject();
        value.put("issue_key", issueKey);
        value.put("severity", severity);
        value.put("title", title);
        value.put("what_happened", whatHappened);
        value.put("what_it_means", whatItMeans);
        value.put("evidence", evidence);
        value.put("next_action", nextAction);
        value.put("action_label", actionLabel);
        value.put("action_kind", actionKind);
        value.put("run_id", runId);
        value.put("repository_id", repositoryId);
        value.put("validation_session_id", validationSessionId);
        return value;
    }

    String plainText() {
        StringBuilder text = new StringBuilder();
        text.append(title).append('\n');
        text.append("issue_key=").append(issueKey).append('\n');
        text.append("severity=").append(severity).append('\n');
        if (!runId.isEmpty()) text.append("run_id=").append(runId).append('\n');
        if (!repositoryId.isEmpty()) text.append("repository_id=").append(repositoryId).append('\n');
        if (!validationSessionId.isEmpty()) text.append("validation_session_id=").append(validationSessionId).append('\n');
        text.append("what_happened=").append(whatHappened).append('\n');
        text.append("what_it_means=").append(whatItMeans).append('\n');
        text.append("evidence=").append(evidence).append('\n');
        text.append("safe_next_action=").append(nextAction).append('\n');
        text.append("action=").append(actionLabel).append(" [").append(actionKind).append("]\n");
        return text.toString();
    }

    private static void addEnvironmentFixes(Context context, DebugWorkbenchHealth.Snapshot snapshot,
                                            List<DebugGuidedFix> fixes, Set<String> seen) {
        for (DebugWorkbenchHealth.Component component : snapshot.components) {
            String name = clean(component.name).toLowerCase(Locale.ROOT);
            if (name.equals("termux") && component.status.equals("fail")) {
                add(fixes, seen, create("termux_missing", "error", "Install or expose Termux",
                        "The app cannot see the Termux package.",
                        "Devtool cannot launch commands until Termux is installed and visible to the Android app.",
                        component.detail, "Install Termux or open Android app settings to verify package visibility.",
                        "Open settings diagnostics", "settings"));
            } else if (name.equals("termux runcommand") && component.status.equals("fail")) {
                add(fixes, seen, create("termux_run_command_permission_missing", "error", "Grant Termux RUN_COMMAND",
                        "Termux is installed, but the app cannot request command execution.",
                        "Run requests will stop before the shell wrapper can start.",
                        component.detail, "Grant com.termux.permission.RUN_COMMAND, then run the bridge roundtrip probe.",
                        "Open app permission settings", "settings"));
            } else if (name.equals("bridge token") && !component.status.equals("pass")) {
                add(fixes, seen, create("bridge_token_mismatch", statusSeverity(component.status), "Refresh bridge token markers",
                        "The app and Termux bridge token state are not aligned.",
                        "Callbacks may be rejected even when Termux successfully starts a command.",
                        component.label + " · " + component.detail,
                        "Update the Termux bridge token, then run the bridge roundtrip probe.",
                        "Run bridge roundtrip probe", "bridge_probe"));
            } else if (name.equals("bridge roundtrip") && component.status.equals("fail")) {
                add(fixes, seen, create("bridge_roundtrip_callback_missing", "error", "Repair bridge roundtrip",
                        "The app could not complete the Android → Termux → Android callback chain.",
                        "The failure is before or inside the command-launch bridge, not inside devtool itself.",
                        component.label + " · " + component.detail,
                        "Run the bridge probe again after checking Termux permission and token markers.",
                        "Run bridge roundtrip probe", "bridge_probe"));
            } else if (name.equals("notifications") && component.status.equals("warn")) {
                add(fixes, seen, create("notification_blocked", "warning", "Enable progress notifications",
                        "Android notification access is not fully ready.",
                        "Long-running runs may work, but progress/error surfacing can disappear into the couch cushions.",
                        component.detail, "Open app notification settings and allow Devtool notifications.",
                        "Open app notification settings", "notification_settings"));
            } else if (name.equals("root bridge") && component.status.equals("fail")) {
                add(fixes, seen, create("root_bridge_unavailable", "error", "Repair root bridge",
                        "The root bridge is reporting a failing state.",
                        "Root/chroot repository actions may fail before Git or validation commands can start.",
                        component.label + " · " + component.detail,
                        "Open Settings diagnostics and refresh or repair the root bridge pairing.",
                        "Open Settings diagnostics", "settings"));
            }
        }
    }

    private static void addRunFix(DebugWorkbenchHealth.Snapshot snapshot, List<DebugGuidedFix> fixes, Set<String> seen) {
        RunRecord run = snapshot.activeRun != null ? snapshot.activeRun : snapshot.latestRun;
        if (run == null) return;
        String evidence = join(run.launchLog, run.detail, run.error, run.stderr, run.phase);
        String lower = evidence.toLowerCase(Locale.ROOT);
        DebugGuidedFix fix = null;
        if (lower.contains("termux_intent_sent") && !lower.contains("termux_wrapper_started")) {
            fix = create("termux_wrapper_never_started", "error", "Termux wrapper never started",
                    "Android sent the RUN_COMMAND intent, but no shell-side wrapper checkpoint arrived.",
                    "The command likely failed before devtool itself started.", evidence,
                    "Check Termux RUN_COMMAND permission, then run the bridge roundtrip probe.",
                    "Run bridge roundtrip probe", "bridge_probe");
        } else if (lower.contains("workdir_missing") || lower.contains("workdir missing")) {
            fix = create("workdir_missing", "error", "Fix missing workdir",
                    "The Termux wrapper could not enter the requested working directory.",
                    "The run path is wrong for the selected execution namespace.", evidence,
                    "Open the run log, copy the trace, then repair the repository/path metadata if needed.",
                    "Open run log", "run_log");
        } else if (lower.contains("command_not_found") || lower.contains("command not found")) {
            fix = create("devtool_binary_missing", "error", "Fix missing devtool binary",
                    "The wrapper reached command lookup but did not find the requested binary.",
                    "Termux started correctly, but the shell environment cannot resolve devtool.", evidence,
                    "Open Termux and verify command -v devtool in the same shell path.",
                    "Open Termux", "termux");
        } else if ((run.status.equals("running") || run.status.equals("waiting"))
                && lower.contains("exec_starting") && !lower.contains("first_progress_event_received")) {
            fix = create("run_started_no_progress", "warning", "Run started but no progress arrived",
                    "The wrapper reached EXEC_STARTING, but no authenticated progress event arrived yet.",
                    "Devtool may be hung, killed, or emitting output that the bridge parser did not classify.", evidence,
                    "Open the run log and recent debug events to compare command output with bridge progress.",
                    "Open run log", "run_log");
        } else if (run.status.equals("failed") && !evidence.trim().isEmpty()) {
            String key = DebugIssueClassifier.classifyMessage(evidence);
            fix = fromIssue(key, "error", "Run failed", evidence, run.runId, "", "");
        }
        if (fix != null) {
            fix.runId = run.runId;
            add(fixes, seen, fix);
        }
    }

    private static void addValidationFix(DebugWorkbenchHealth.Snapshot snapshot, List<DebugGuidedFix> fixes, Set<String> seen) {
        ValidationSessionRecord session = snapshot.activeValidation != null ? snapshot.activeValidation : snapshot.latestValidation;
        if (session == null) return;
        String evidence = join(session.effectiveState, session.lastError, session.lastReason, session.lastMessage, session.rollbackError);
        String lower = evidence.toLowerCase(Locale.ROOT);
        DebugGuidedFix fix = null;
        if (lower.contains("stale") || lower.contains("revision")) {
            fix = create("validation_stale_revision", "warning", "Open the current validation session",
                    "A validation action referenced stale or out-of-date session state.",
                    "The old notification or UI event should not mutate the current validation graph.", evidence,
                    "Open Validation Workbench and use the current revision controls.",
                    "Open Validation Workbench", "validation_workbench");
        } else if (session.blocked() || session.recoveryRequired() || session.effectiveState.equals("failed")) {
            fix = create("validation_phase_failed", "error", "Review failed validation phase",
                    "Validation is blocked, failed, or waiting for recovery.",
                    "The next safe action depends on the failed phase and rollback state.", evidence,
                    "Open Validation Workbench, review evidence, then choose retry/reset from that screen.",
                    "Open Validation Workbench", "validation_workbench");
        }
        if (fix != null) {
            fix.validationSessionId = session.sessionId;
            add(fixes, seen, fix);
        }
    }

    private static void addRepositoryFixes(Context context, DebugWorkbenchHealth.Snapshot snapshot,
                                           List<DebugGuidedFix> fixes, Set<String> seen) {
        int added = 0;
        for (RepositoryRecord repository : snapshot.repositories) {
            if (added >= MAX_REPO_FIXES) break;
            RepositoryPathResolution path = RepositoryPathResolver.resolve(repository);
            RepoQueueRecovery.QueueHealth queue = RepoQueueRecovery.assess(context, repository);
            DebugGuidedFix fix = null;
            if (!path.status.equals(RepositoryPathResolution.STATUS_PASS)) {
                fix = create(path.issueKey, statusSeverity(path.status), path.title,
                        path.summary,
                        "Repository metadata may point at the wrong execution namespace, chroot, user, or Git root.",
                        path.plainText(),
                        path.recommendation,
                        path.hasRepairActions() ? "Open repository repair" : "Open repository doctor",
                        "repository");
            } else if (!queue.status.equals("pass")) {
                String key = queue.orphaned > 0 ? "repo_operation_orphaned"
                        : queue.staleRunning > 0 ? "repo_operation_stale_running"
                        : "repo_operation_stale_queued";
                fix = create(key, statusSeverity(queue.status), "Recover repository queue",
                        queue.summary,
                        "A queued/running/orphaned operation needs review before retrying or marking interrupted.",
                        RepoQueueRecovery.plainText(context, repository),
                        queue.recommendation,
                        "Open queue recovery", "repository");
            } else if (!repository.lastError.trim().isEmpty()) {
                String key = DebugIssueClassifier.classifyMessage(repository.lastError);
                fix = fromIssue(key, "warning", "Review repository error", repository.lastError, "", repository.repositoryId, "");
            }
            if (fix != null) {
                fix.repositoryId = repository.repositoryId;
                add(fixes, seen, fix);
                added++;
            }
        }
    }

    private static void addEventFixes(DebugWorkbenchHealth.Snapshot snapshot, List<DebugGuidedFix> fixes, Set<String> seen) {
        int added = 0;
        for (DebugEventRecord event : snapshot.recentEvents) {
            if (!event.problem()) continue;
            String evidence = join(event.eventType, event.message, event.detailJson, event.surface);
            String key = DebugIssueClassifier.classifyMessage(evidence);
            DebugGuidedFix fix = fromIssue(key, event.severity, event.eventType, evidence,
                    event.runId, event.repositoryId, event.validationSessionId);
            if (fix != null) {
                if (!event.operationId.isEmpty()) fix.evidence = fix.evidence + "\noperation_id=" + event.operationId;
                add(fixes, seen, fix);
                added++;
                if (added >= MAX_EVENT_FIXES) break;
            }
        }
    }

    private static DebugGuidedFix fromIssue(String issueKey, String severity, String fallbackTitle,
                                            String evidence, String runId, String repositoryId,
                                            String validationSessionId) {
        String key = clean(issueKey).isEmpty() ? "generic_event" : issueKey;
        if (key.equals("termux_run_command_permission_missing")) return withIds(create(key, severity, "Grant Termux RUN_COMMAND",
                "Termux rejected or could not accept command execution.",
                "The run cannot reach the wrapper or devtool binary until the permission is granted.", evidence,
                "Open app permission settings, grant RUN_COMMAND, then run the bridge roundtrip probe.",
                "Open app permission settings", "settings"), runId, repositoryId, validationSessionId);
        if (key.equals("termux_wrapper_never_started") || key.equals("bridge_roundtrip_wrapper_missing")) return withIds(create(key, severity, "Termux wrapper never started",
                "Android sent the request, but the shell-side wrapper did not confirm startup.",
                "The failure is between Android intent delivery and shell execution.", evidence,
                "Check Termux background execution/RUN_COMMAND permission and rerun the bridge roundtrip probe.",
                "Run bridge roundtrip probe", "bridge_probe"), runId, repositoryId, validationSessionId);
        if (key.equals("bridge_token_mismatch")) return withIds(create(key, severity, "Refresh bridge token markers",
                "A callback or marker check failed token validation.",
                "Termux may run commands, but the app will reject progress/results until markers match.", evidence,
                "Refresh bridge markers and run the bridge roundtrip probe.",
                "Run bridge roundtrip probe", "bridge_probe"), runId, repositoryId, validationSessionId);
        if (key.equals("termux_workdir_missing") || key.equals("workdir_missing") || key.equals("path_missing")) return withIds(create(key, severity, "Fix missing path/workdir",
                "The selected execution namespace could not see the requested path.",
                "This is usually a Termux/root/chroot path-family mismatch, not a Git failure.", evidence,
                "Open the repository doctor or path repair screen and verify the selected namespace.",
                "Open repository doctor", repositoryId.isEmpty() ? "repositories" : "repository"), runId, repositoryId, validationSessionId);
        if (key.equals("termux_command_not_found_before_exec") || key.equals("devtool_binary_missing")) return withIds(create(key, severity, "Fix missing command binary",
                "The shell wrapper started, but command lookup failed before exec.",
                "Your PATH/environment does not expose the requested binary to this execution route.", evidence,
                "Open Termux and verify command -v devtool, then rerun the bridge probe.",
                "Open Termux", "termux"), runId, repositoryId, validationSessionId);
        if (key.equals("run_started_no_progress") || key.equals("timeout")) return withIds(create(key, severity, "Inspect started run with no progress",
                "The command started, but the app did not receive expected progress afterward.",
                "The process may be hung, killed, or producing output that is not parsed as progress.", evidence,
                "Open the run log and compare stdout/stderr with recent debug events.",
                "Open run log", runId.isEmpty() ? "logs" : "run_log"), runId, repositoryId, validationSessionId);
        if (key.equals("repo_path_wrong_namespace") || key.equals("repo_termux_path_saved_as_chroot")
                || key.equals("repo_chroot_path_saved_as_termux_or_root") || key.equals("repo_root_path_saved_as_termux")) return withIds(create(key, severity, "Repair repository namespace",
                "Repository metadata points at a path family that does not match its executor.",
                "Git will never start correctly until Termux/root/chroot path metadata is aligned.", evidence,
                "Open repository repair and apply only metadata-safe path/environment changes.",
                "Open repository repair", "repository"), runId, repositoryId, validationSessionId);
        if (key.equals("repo_doctor_runuser_failed") || key.equals("repo_runuser_failed")) return withIds(create(key, severity, "Use executor fallback for runuser",
                "User switching failed before Git started.",
                "The path may be valid; the selected chroot user-switch method is the failing piece.", evidence,
                "Open repository doctor and review shell/user-switch candidates before retrying.",
                "Open repository doctor", "repository"), runId, repositoryId, validationSessionId);
        if (key.equals("repo_doctor_shell_missing") || key.equals("repo_shell_missing")) return withIds(create(key, severity, "Install or select a usable shell",
                "The executor could not find a usable shell in the selected namespace.",
                "Git cannot launch until zsh/bash/sh selection succeeds.", evidence,
                "Open repository doctor and use the root/chroot executor planner evidence.",
                "Open repository doctor", "repository"), runId, repositoryId, validationSessionId);
        if (key.equals("repo_doctor_git_missing") || key.equals("repo_git_missing") || key.equals("git_binary_confirmation_failed")) return withIds(create(key, severity, "Install Git in the selected namespace",
                "The executor could not confirm a git binary before the operation.",
                "The repository path may be valid, but Git is unavailable where the command runs.", evidence,
                "Install Git in Termux/chroot or switch the repository executor to one that has Git.",
                "Open repository doctor", "repository"), runId, repositoryId, validationSessionId);
        if (key.equals("repo_operation_orphaned") || key.equals("repo_queue_health_failed")) return withIds(create(key, severity, "Recover orphaned repository operation",
                "A Git operation appears detached from the Android app lifecycle.",
                "The app should not guess; review whether the process is alive before terminating or marking interrupted.", evidence,
                "Open queue recovery and choose terminate-orphan or mark-interrupted only after review.",
                "Open queue recovery", "repository"), runId, repositoryId, validationSessionId);
        if (key.equals("validation_stale_revision") || key.equals("stale_bridge_update")) return withIds(create(key, severity, "Use the current validation controls",
                "A stale update/action was detected and ignored.",
                "This protects the active validation graph from old notifications or delayed callbacks.", evidence,
                "Open Validation Workbench and use controls from the current session revision.",
                "Open Validation Workbench", "validation_workbench"), runId, repositoryId, validationSessionId);
        if (key.equals("notification_blocked")) return withIds(create(key, severity, "Enable notification access",
                "Android notification state may block reliable progress surfacing.",
                "Runs can still execute, but long-running feedback may be invisible.", evidence,
                "Open app notification settings and allow Devtool notifications.",
                "Open app notification settings", "notification_settings"), runId, repositoryId, validationSessionId);
        if (key.equals("generic_event") || key.equals("generic_failure")) return withIds(create(key, severity, clean(fallbackTitle).isEmpty() ? "Review debug event" : fallbackTitle,
                "A warning/error debug event was recorded.",
                "No specialized guided fix matched this issue yet, but the evidence is copyable.", evidence,
                "Copy the diagnosis and open the relevant log, repository, or validation screen.",
                "Copy diagnosis", "copy"), runId, repositoryId, validationSessionId);
        return withIds(create(key, severity, clean(fallbackTitle).isEmpty() ? key : fallbackTitle,
                "Devtool recognized issue key " + key + ".",
                "Use the linked diagnostic surface to inspect the retained evidence before acting.", evidence,
                "Open the relevant diagnostic surface and copy the report before retrying.",
                repositoryId.isEmpty() ? "Open Debug Workbench" : "Open repository doctor",
                repositoryId.isEmpty() ? "debug_workbench" : "repository"), runId, repositoryId, validationSessionId);
    }

    private static DebugGuidedFix create(String issueKey, String severity, String title, String whatHappened,
                                         String whatItMeans, String evidence, String nextAction,
                                         String actionLabel, String actionKind) {
        DebugGuidedFix fix = new DebugGuidedFix();
        fix.issueKey = clean(issueKey).isEmpty() ? "generic_event" : clean(issueKey);
        fix.severity = normalizeSeverity(severity);
        fix.title = clean(title);
        fix.whatHappened = clean(whatHappened);
        fix.whatItMeans = clean(whatItMeans);
        fix.evidence = compactEvidence(evidence);
        fix.nextAction = clean(nextAction);
        fix.actionLabel = clean(actionLabel);
        fix.actionKind = clean(actionKind).isEmpty() ? "debug_workbench" : clean(actionKind);
        return fix;
    }

    private static DebugGuidedFix withIds(DebugGuidedFix fix, String runId, String repositoryId, String validationSessionId) {
        fix.runId = clean(runId);
        fix.repositoryId = clean(repositoryId);
        fix.validationSessionId = clean(validationSessionId);
        return fix;
    }

    private static void add(List<DebugGuidedFix> fixes, Set<String> seen, DebugGuidedFix fix) {
        if (fix == null) return;
        String key = fix.issueKey + "|" + fix.runId + "|" + fix.repositoryId + "|" + fix.validationSessionId;
        if (seen.contains(key)) return;
        seen.add(key);
        fixes.add(fix);
    }

    private static ArrayList<DebugGuidedFix> limit(ArrayList<DebugGuidedFix> fixes, int max) {
        ArrayList<DebugGuidedFix> limited = new ArrayList<>();
        for (DebugGuidedFix fix : fixes) {
            limited.add(fix);
            if (limited.size() >= max) break;
        }
        return limited;
    }

    private static String statusSeverity(String status) {
        String value = clean(status).toLowerCase(Locale.ROOT);
        if (value.equals("fail") || value.equals("failed") || value.equals("error") || value.equals("fatal")) return "error";
        if (value.equals("pass") || value.equals("ok") || value.equals("success")) return "info";
        return "warning";
    }

    private static String normalizeSeverity(String severity) {
        String value = clean(severity).toLowerCase(Locale.ROOT);
        if (value.equals("fatal")) return "fatal";
        if (value.equals("error") || value.equals("fail") || value.equals("failed")) return "error";
        if (value.equals("success") || value.equals("pass") || value.equals("info")) return "info";
        return "warning";
    }

    private static String compactEvidence(String value) {
        String text = clean(value).replace("\r", "").trim();
        if (text.isEmpty()) return "No detailed evidence was retained.";
        return text.length() <= 1200 ? text : text.substring(0, 1200) + "\n…";
    }

    private static String join(String... values) {
        StringBuilder text = new StringBuilder();
        for (String value : values) {
            String clean = clean(value);
            if (clean.isEmpty()) continue;
            if (text.length() > 0) text.append('\n');
            text.append(clean);
        }
        return text.toString();
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
