package io.github.mikeceead.devtoolhud;

import android.content.Intent;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

final class HudUpdate {
    final String runId;
    final long sequence;
    final long updatedAtMs;
    final String status;
    final String task;
    final String artifact;
    final String phase;
    final String detail;
    final String repo;
    final String projectType;
    final String executionEnvironment;
    final String target;
    final String workspace;
    final int workspaceIndex;
    final int workspaceTotal;
    final String workspacesJson;
    final List<Workspace> workspaces;
    final int phaseIndex;
    final int phaseTotal;
    final String commit;
    final String summary;
    final int progress;
    final int phaseProgress;
    final int current;
    final int total;
    final String unitLabel;
    final boolean indeterminate;
    final int passed;
    final int failed;
    final int skipped;
    final int testTotal;
    final int testAssemblies;
    final int warnings;
    final int errors;
    final List<Step> steps;
    final String stepsJson;
    final long startedAtMs;
    final long durationMs;
    final boolean overlayRequested;
    final UiSemanticState ui;

    private HudUpdate(
            String runId, long sequence, long updatedAtMs,
            String status, String task, String artifact, String phase, String detail,
            String repo, String projectType, String executionEnvironment, String target, String workspace, int workspaceIndex, int workspaceTotal, String workspacesJson, int phaseIndex, int phaseTotal,
            String commit, String summary,
            int progress, int phaseProgress, int current, int total, String unitLabel,
            boolean indeterminate,
            int passed, int failed, int skipped, int testTotal, int testAssemblies,
            int warnings, int errors,
            String stepsJson, long startedAtMs, long durationMs, boolean overlayRequested, UiSemanticState ui) {
        this.runId = safeRunId(runId);
        this.sequence = Math.max(0L, sequence);
        this.updatedAtMs = updatedAtMs > 0L ? updatedAtMs : System.currentTimeMillis();
        this.status = normalizeStatus(status);
        this.task = clean(task, humanTask(phase));
        this.artifact = clean(artifact, "");
        this.phase = clean(phase, "Working");
        this.detail = clean(detail, "Devtool task in progress");
        this.repo = clean(repo, "devtool");
        this.projectType = normalizeProjectType(projectType);
        this.executionEnvironment = normalizeExecutionEnvironment(executionEnvironment);
        this.target = clean(target, "");
        this.workspace = clean(workspace, "");
        this.workspaceIndex = Math.max(0, workspaceIndex);
        this.workspaceTotal = Math.max(0, workspaceTotal);
        this.workspacesJson = clean(workspacesJson, "");
        this.workspaces = parseWorkspaces(this.workspacesJson);
        this.phaseIndex = Math.max(0, phaseIndex);
        this.phaseTotal = Math.max(0, phaseTotal);
        this.commit = clean(commit, "");
        this.summary = clean(summary, "");
        this.progress = clamp(progress);
        this.phaseProgress = clamp(phaseProgress);
        this.current = Math.max(-1, current);
        this.total = Math.max(-1, total);
        this.unitLabel = clean(unitLabel, "items");
        this.indeterminate = indeterminate;
        this.passed = Math.max(0, passed);
        this.failed = Math.max(0, failed);
        this.skipped = Math.max(0, skipped);
        this.testTotal = Math.max(0, testTotal);
        this.testAssemblies = Math.max(0, testAssemblies);
        this.warnings = Math.max(0, warnings);
        this.errors = Math.max(0, errors);
        this.ui = ui == null ? UiSemanticState.empty() : ui;
        this.stepsJson = clean(stepsJson, "");
        List<Step> semanticSteps = this.ui.progressSteps();
        this.steps = semanticSteps.isEmpty() ? parseSteps(this.stepsJson, this.phase, this.status, this.phaseProgress) : semanticSteps;
        this.startedAtMs = startedAtMs > 0 ? startedAtMs : System.currentTimeMillis();
        this.durationMs = Math.max(0L, durationMs);
        this.overlayRequested = overlayRequested;
    }

    static HudUpdate fromIntent(Intent intent) {
        int percent = intent.getIntExtra("progress", -1);
        int current = intent.getIntExtra("current", -1);
        int total = intent.getIntExtra("total", -1);
        if (percent < 0 && current >= 0 && total > 0) {
            percent = (int) Math.round((current * 100.0) / total);
        }
        int phasePercent = intent.getIntExtra("phase_progress", -1);
        if (phasePercent < 0 && current >= 0 && total > 0) {
            phasePercent = (int) Math.round((current * 100.0) / total);
        }
        boolean indeterminate = intent.getBooleanExtra("indeterminate", percent < 0);
        return new HudUpdate(
                intent.getStringExtra("run_id"),
                intent.getLongExtra("sequence", 0L),
                intent.getLongExtra("updated_at_ms", 0L),
                intent.getStringExtra("status"),
                intent.getStringExtra("task"),
                intent.getStringExtra("artifact"),
                intent.getStringExtra("phase"),
                intent.getStringExtra("detail"),
                intent.getStringExtra("repo"),
                intent.getStringExtra("project_type"),
                intent.getStringExtra("execution_environment"),
                intent.getStringExtra("target"),
                intent.getStringExtra("workspace"),
                intent.getIntExtra("workspace_index", 0),
                intent.getIntExtra("workspace_total", 0),
                intent.getStringExtra("workspaces_json"),
                intent.getIntExtra("phase_index", 0),
                intent.getIntExtra("phase_total", 0),
                intent.getStringExtra("commit"),
                intent.getStringExtra("summary"),
                Math.max(0, percent), Math.max(0, phasePercent), current, total,
                intent.getStringExtra("unit_label"), indeterminate,
                intent.getIntExtra("passed", 0),
                intent.getIntExtra("failed", 0),
                intent.getIntExtra("skipped", 0),
                intent.getIntExtra("test_total", 0),
                intent.getIntExtra("test_assemblies", 0),
                intent.getIntExtra("warnings", 0),
                intent.getIntExtra("errors", 0),
                intent.getStringExtra("steps_json"),
                intent.getLongExtra("started_at_ms", 0L),
                intent.getLongExtra("duration_ms", 0L),
                intent.getBooleanExtra("overlay", false), UiSemanticState.fromIntent(intent));
    }

    static HudUpdate sample() {
        return new HudUpdate("setup-test", 7L, System.currentTimeMillis(),
                "running", "Applying overlay bundle", "demo_bundle.zip",
                "Testing", "Running test assembly 2 of 3",
                "sims3tools", "dotnet", "native-termux", "S3Atelier.Modern.sln", "s3atelier_avalonia", 1, 2, "", 3, 4,
                "", "", 72, 66, 2, 3, "test assemblies", false,
                160, 0, 1, 161, 2, 2, 0,
                sampleSteps(), System.currentTimeMillis() - 83000L, 0L, false, UiSemanticState.empty());
    }

    static HudUpdate sampleOverlay() {
        HudUpdate sample = sample();
        return new HudUpdate(sample.runId + "-overlay", sample.sequence, sample.updatedAtMs,
                sample.status, sample.task, sample.artifact, sample.phase, sample.detail,
                sample.repo, sample.projectType, sample.executionEnvironment, sample.target, sample.workspace, sample.workspaceIndex, sample.workspaceTotal, sample.workspacesJson, sample.phaseIndex, sample.phaseTotal,
                sample.commit, sample.summary, sample.progress, sample.phaseProgress,
                sample.current, sample.total, sample.unitLabel, sample.indeterminate,
                sample.passed, sample.failed, sample.skipped, sample.testTotal, sample.testAssemblies,
                sample.warnings, sample.errors, sample.stepsJson, sample.startedAtMs,
                sample.durationMs, true, sample.ui);
    }

    static HudUpdate validation(ValidationSessionRecord session) {
        ValidationPhaseRecord focus = session.activePhase();
        if (focus == null) focus = session.failedPhase();
        if (focus == null) focus = session.nextPhase();
        String phase = focus == null ? (session.validated() ? "Validated" : "Validation") : focus.label;
        String detail;
        if (session.blocked()) detail = focus == null ? "Validation is blocked" : firstUseful(focus.failureSummary, session.lastError, session.lastMessage, "Review the failed phase");
        else if (session.effectiveState.equals("pausing")) detail = "Finishing the current phase before pausing";
        else if (session.effectiveState.equals("stopping")) detail = "Stopping the active phase safely";
        else if (session.paused()) detail = firstUseful(session.pausedReason, session.lastMessage, "", "Ready to continue");
        else if (session.state.equals("validated-uncommitted")) detail = "Validation passed; Git commit still needs attention";
        else if (session.validated()) detail = "All required validation phases passed";
        else detail = firstUseful(session.lastMessage, "", "", "Validation is running");
        String status = session.blocked() ? "failed" : session.validated() ? "ok"
                : session.paused() ? "waiting" : "running";
        int passed = 0;
        int skipped = 0;
        org.json.JSONArray steps = new org.json.JSONArray();
        for (ValidationPhaseRecord item : session.phases) {
            if (item.status.equals("passed") || item.status.equals("not-applicable")) passed++;
            if (item.status.equals("skipped")) skipped++;
            org.json.JSONObject step = new org.json.JSONObject();
            try {
                step.put("key", item.key);
                step.put("label", item.label);
                step.put("status", item.passed() ? (item.status.equals("skipped") ? "skipped" : "ok")
                        : item.failed() ? "failed" : item.running() ? "running" : "pending");
                step.put("progress", item.passed() ? 100 : item.progress);
                steps.put(step);
            } catch (org.json.JSONException ignored) {}
        }
        int current = focus == null ? -1 : focus.index;
        int total = session.phaseTotal > 0 ? session.phaseTotal : session.phases.size();
        String summary = session.title() + " validation\n"
                + session.completedPhases + " of " + total + " phases complete\n"
                + detail + "\nChanges " + (session.recoveryAvailable ? "and rollback point retained" : "retained");
        return new HudUpdate("validation:" + session.sessionId, session.revision, session.updatedAtMs,
                status, session.title() + " validation", session.artifact, phase, detail,
                session.projectRoot, session.projectType, session.executionEnvironment, session.target,
                session.scopeMode, 0, 0, "", current, total, "", summary,
                session.overallProgress(), focus == null ? session.overallProgress() : itemProgress(focus),
                current, total, "phases", focus != null && focus.running() && !focus.supportsProgress,
                passed, session.blocked() ? 1 : 0, skipped, passed + skipped + (session.blocked() ? 1 : 0), 0,
                session.state.equals("validated-uncommitted") ? 1 : 0, session.blocked() ? 1 : 0,
                steps.toString(), session.createdAtMs, Math.max(0L, session.updatedAtMs - session.createdAtMs), false, UiSemanticState.empty());
    }

    private static int itemProgress(ValidationPhaseRecord phase) {
        if (phase.passed()) return 100;
        return Math.max(0, Math.min(100, phase.progress));
    }

    boolean validationSession() { return runId.startsWith("validation:"); }
    String validationSessionId() { return validationSession() ? runId.substring("validation:".length()) : ""; }

    static HudUpdate commandStarted(String runId, String command, String workdir, boolean overlay) {
        return new HudUpdate(runId, 0L, System.currentTimeMillis(),
                "waiting", "Starting Devtool", "", "Preparing",
                "RunCommand request sent to Termux", "Termux", "auto", "native-termux", clean(workdir, "~/"), "", 0, 0, "", 0, 0,
                "", clean(command, ""), 0, 0, -1, -1, "items", true,
                0, 0, 0, 0, 0, 0, 0, "", System.currentTimeMillis(), 0L, overlay, UiSemanticState.empty());
    }

    static HudUpdate commandWaiting(String runId, String command, String workdir,
                                    boolean background, boolean overlay, long startedAtMs) {
        String mode = background ? "background" : "foreground";
        return new HudUpdate(runId, 0L, System.currentTimeMillis(),
                "waiting", "Waiting for live progress", "", "Preparing",
                "Termux is running in " + mode + " mode; no bridge update received yet",
                "Termux", "auto", "native-termux", clean(workdir, "~/"), "", 0, 0, "", 0, 0,
                "", clean(command, ""), 0, 0, -1, -1, "items", true,
                0, 0, 0, 0, 0, 0, 0, "", startedAtMs, 0L, overlay, UiSemanticState.empty());
    }

    static HudUpdate commandFinished(String runId, int exitCode, String stdout, String stderr,
                                     String error, boolean overlay) {
        boolean ok = exitCode == 0 && clean(error, "").isEmpty();
        String detail = firstUseful(error, stderr, stdout, ok ? "Command completed" : "Command failed");
        return new HudUpdate(runId, Long.MAX_VALUE, System.currentTimeMillis(),
                ok ? "ok" : "failed", ok ? "Devtool completed" : "Devtool failed", "", "Termux",
                detail, "Termux", "auto", "native-termux", "exit " + exitCode, "", 0, 0, "", 0, 0,
                "", "", 100, 100, -1, -1, "items", false,
                0, ok ? 0 : 1, 0, ok ? 0 : 1, 0, 0, ok ? 0 : 1, "",
                System.currentTimeMillis(), 0L, overlay, UiSemanticState.empty());
    }

    private static String firstUseful(String first, String second, String third, String fallback) {
        for (String value : new String[]{first, second, third}) {
            String cleaned = plainText(clean(value, ""));
            if (!cleaned.isEmpty()) return cleaned.length() > 180 ? cleaned.substring(0, 177) + "…" : cleaned;
        }
        return fallback;
    }

    static final class Workspace {
        final String name;
        final String status;
        final String meta;
        final int progress;

        Workspace(String name, String status, String meta, int progress) {
            this.name = clean(name, "Workspace");
            this.status = clean(status, "pending").toLowerCase(Locale.ROOT);
            this.meta = clean(meta, "Waiting");
            this.progress = clamp(progress);
        }

        boolean active() { return status.equals("active") || status.equals("running"); }
        boolean done() { return status.equals("done") || status.equals("ok"); }
        boolean failed() { return status.equals("failed"); }
    }

    static List<Workspace> parseWorkspaces(String value) {
        List<Workspace> result = new ArrayList<>();
        String raw = clean(value, "");
        if (raw.isEmpty()) return result;
        try {
            JSONArray array = new JSONArray(raw);
            for (int i = 0; i < array.length(); i++) {
                JSONObject item = array.optJSONObject(i);
                if (item == null) continue;
                result.add(new Workspace(
                        item.optString("name", "Workspace"),
                        item.optString("status", "pending"),
                        item.optString("meta", "Waiting"),
                        item.optInt("progress", 0)));
            }
        } catch (JSONException | RuntimeException ignored) {
            result.clear();
        }
        return result;
    }

    static final class Step {
        final String key;
        final String label;
        final String status;
        final int progress;

        Step(String key, String label, String status, int progress) {
            this.key = clean(key, "step");
            this.label = clean(label, "Step");
            this.status = clean(status, "pending").toLowerCase(Locale.ROOT);
            this.progress = clamp(progress);
        }

        boolean running() { return status.equals("running"); }
        boolean succeeded() { return status.equals("ok"); }
        boolean failed() { return status.equals("failed"); }
        boolean skipped() { return status.equals("skipped"); }
    }

    String stepsJsonForStore() {
        JSONArray array = new JSONArray();
        for (Step step : steps) {
            JSONObject item = new JSONObject();
            try {
                item.put("key", step.key);
                item.put("label", step.label);
                item.put("status", step.status);
                item.put("progress", step.progress);
                array.put(item);
            } catch (JSONException ignored) {}
        }
        return array.length() > 0 ? array.toString() : stepsJson;
    }

    static List<Step> parseSteps(String value, String phase, String status, int progress) {
        List<Step> result = new ArrayList<>();
        String raw = clean(value, "");
        if (!raw.isEmpty()) {
            try {
                JSONArray array = new JSONArray(raw);
                for (int i = 0; i < array.length(); i++) {
                    JSONObject item = array.optJSONObject(i);
                    if (item == null) continue;
                    result.add(new Step(
                            item.optString("key", "step"),
                            item.optString("label", "Step"),
                            item.optString("status", "pending"),
                            item.optInt("progress", 0)));
                }
            } catch (JSONException | RuntimeException ignored) {
                result.clear();
            }
        }
        if (result.isEmpty()) {
            String fallback = normalizeStatus(status);
            String stepStatus = fallback.equals("ok") ? "ok" : fallback.equals("failed") ? "failed" : "running";
            result.add(new Step("current", clean(phase, "Working"), stepStatus, progress));
        }
        return result;
    }

    private static String sampleSteps() {
        return "[{\"key\":\"extract\",\"label\":\"Extract\",\"status\":\"ok\",\"progress\":100},"
                + "{\"key\":\"restore\",\"label\":\"Restore · s3atelier\",\"status\":\"ok\",\"progress\":100},"
                + "{\"key\":\"build\",\"label\":\"Build · s3atelier\",\"status\":\"ok\",\"progress\":100},"
                + "{\"key\":\"test\",\"label\":\"Test · s3atelier\",\"status\":\"running\",\"progress\":66},"
                + "{\"key\":\"commit\",\"label\":\"Commit\",\"status\":\"pending\",\"progress\":0}]";
    }

    String effectiveStatus() {
        return normalizeStatus(ui.operationStatus(status));
    }

    boolean isTerminal() {
        String effective = effectiveStatus();
        return effective.equals("ok") || effective.equals("failed") || effective.equals("interrupted")
                || effective.equals("cancelled") || effective.equals("skipped");
    }

    boolean succeeded() { return effectiveStatus().equals("ok"); }
    boolean failedState() { return effectiveStatus().equals("failed"); }
    boolean rollbackState() { return phase.toLowerCase(Locale.ROOT).contains("rollback"); }

    boolean isApplyOperation() {
        String haystack = (task + " " + phase + " " + artifact + " " + summary).toLowerCase(Locale.ROOT);
        return haystack.contains("overlay") || haystack.contains("snapshot");
    }

    String applyToastText() {
        String subject = !artifact.isEmpty() ? shortName(artifact)
                : task.toLowerCase(Locale.ROOT).contains("snapshot") ? "snapshot"
                : task.toLowerCase(Locale.ROOT).contains("bundle") ? "overlay bundle"
                : "overlay";
        if (rollbackState()) return "Rolled back: " + subject;
        if (failedState()) return "Failed: " + subject;
        if (succeeded()) return "Applied: " + subject;
        return "Devtool: " + subject;
    }

    int displayProgress() {
        int semantic = ui.progressPercent(-1);
        if (semantic >= 0) return semantic;
        if (succeeded()) return 100;
        return progress;
    }

    int activeStepProgress() {
        Step active = activeStep();
        return active == null ? phaseProgress : active.progress;
    }

    String phaseLabel() {
        String semantic = ui.operationPhase("");
        if (!semantic.isEmpty()) return semantic;
        if (rollbackState()) return "Rollback";
        if (succeeded()) return "Completed";
        if (failedState()) return "Failed";
        if (status.equals("interrupted") || status.equals("cancelled")) return "Interrupted";
        if (status.equals("waiting")) return "Preparing";
        return humanPhase(phase);
    }

    String title() {
        // Legacy source-contract fallback marker: return "Devtool · " + phaseLabel()
        String semantic = ui.operationTitle("");
        return semantic.isEmpty() ? "Devtool · " + phaseLabel() : semantic;
    }

    String taskTitle() {
        if (rollbackState()) {
            if (succeeded()) return "Changes rolled back";
            if (failedState()) return "Rollback failed";
            return "Rolling back changes";
        }
        if (succeeded()) return "Devtool completed";
        if (failedState()) return "Devtool failed";
        return clean(task, humanTask(phase));
    }

    String contentText() {
        String semantic = ui.operationDetail("");
        if (!semantic.isEmpty()) return semantic;
        String scope = scopeText();
        if (isTerminal()) {
            int tests = passed + failed + skipped;
            if (tests > 0) return scope + " · " + passed + " passed · " + failed + " failed";
            return scope + " · " + detail;
        }
        return scope + " · " + localProgressText();
    }

    String subText() {
        StringBuilder text = new StringBuilder();
        if (current >= 0 && total > 0) {
            text.append(current).append("/").append(total).append(" ").append(unitLabel).append(" · ");
        }
        text.append(displayProgress()).append("% · ").append(elapsedText());
        if (errors > 0) text.append(" · ").append(errors).append(" error").append(errors == 1 ? "" : "s");
        else if (warnings > 0) text.append(" · ").append(warnings).append(" warning").append(warnings == 1 ? "" : "s");
        else text.append(" · 0 errors");
        return text.toString();
    }

    List<String> expandedLines() {
        List<String> lines = new ArrayList<>();
        if (!artifact.isEmpty()) lines.add(shortName(artifact));
        if (workspaceTotal > 1) {
            lines.add("Workspace " + Math.max(1, workspaceIndex) + " of " + workspaceTotal + " · " + scopeText());
        } else {
            lines.add(scopeText());
        }
        lines.add("Runner · " + runnerLabel() + " · " + environmentLabel());
        for (Step step : steps) {
            String mark = step.succeeded() ? "[done]" : step.failed() ? "[failed]" : step.running() ? "[running]" : "[waiting]";
            StringBuilder line = new StringBuilder(mark).append(" ").append(step.label);
            if (step.running()) {
                if (current >= 0 && total > 0 && step == activeStep()) {
                    line.append(" · ").append(current).append("/").append(total).append(" ").append(unitLabel);
                } else if (step.progress > 0) {
                    line.append(" · ").append(step.progress).append("%");
                }
            }
            lines.add(line.toString());
        }
        int tests = passed + failed + skipped;
        if (tests > 0 || testAssemblies > 0) {
            StringBuilder testLine = new StringBuilder("Tests · ")
                    .append(passed).append(" passed · ").append(failed).append(" failed");
            if (skipped > 0) testLine.append(" · ").append(skipped).append(" skipped");
            if (testAssemblies > 0) testLine.append(" · ").append(testAssemblies).append(" ").append(testContainerLabel().toLowerCase(Locale.ROOT));
            lines.add(testLine.toString());
        }
        lines.add("Health · " + warnings + " warnings · " + errors + " errors");
        return lines;
    }

    String expandedText() {
        return String.join("\n", expandedLines());
    }

    String copySummaryText() {
        if (!summary.isEmpty()) return summary;
        return taskTitle() + "\n" + contentText() + "\n" + subText() + "\n\n" + expandedText();
    }

    String criticalText() {
        if (succeeded()) return "Done";
        if (failedState()) return "Failed";
        if (rollbackState()) return "Rollback";
        if (current >= 0 && total > 0) return current + "/" + total;
        Step active = activeStep();
        if (active != null) return active.label;
        if (indeterminate) return phase;
        return displayProgress() + "%";
    }

    int pipelineProgress() {
        if (steps.isEmpty()) return displayProgress();
        double totalProgress = 0.0;
        for (Step step : steps) {
            if (step.succeeded() || step.failed() || step.skipped()) totalProgress += 100.0;
            else totalProgress += step.progress;
        }
        return clamp((int) Math.round(totalProgress / steps.size()));
    }

    Step activeStep() {
        for (Step step : steps) if (step.running()) return step;
        return null;
    }


    String notificationMode() {
        String semanticStatus = ui.operationStatus("");
        String attention = ui.attentionLevel();
        if (attention.equals("critical")) return "failed";
        if (attention.equals("important") && !semanticStatus.equals("failed")) return "warning";
        if (attention.equals("success")) return "success";
        if (validationSession()) {
            ValidationSessionRecord session = ValidationNotificationContext.lookup(validationSessionId());
            if (session != null) {
                if (session.blocked()) return "failed";
                if (session.state.equals("validated-uncommitted")) return "warning";
                if (session.validated()) return "success";
                if (session.paused()) return "stopped";
                return "running";
            }
        }
        if (rollbackState()) {
            if (failedState()) return "failed";
            if (succeeded()) return "recovered";
            return "rollback";
        }
        if (failedState()) return "failed";
        if (succeeded()) return warnings > 0 ? "warning" : "success";
        if (status.equals("cancelled") || status.equals("interrupted") || status.equals("skipped")) return "stopped";
        if (warnings > 0 && errors == 0) return "warning";
        return "running";
    }

    String notificationStateLabel() {
        if (validationSession()) {
            ValidationSessionRecord session = ValidationNotificationContext.lookup(validationSessionId());
            if (session != null) {
                if (session.effectiveState.equals("pausing")) return "PAUSING";
                if (session.effectiveState.equals("stopping")) return "STOPPING";
                if (session.blocked()) return "BLOCKED";
                if (session.state.equals("validated-uncommitted")) return "ACTION REQUIRED";
                if (session.validated()) return "VALIDATED";
                if (session.paused()) return "PAUSED";
                return "LIVE VALIDATION";
            }
        }
        String mode = notificationMode();
        if (mode.equals("success")) return "COMPLETE";
        if (mode.equals("warning")) return isTerminal() ? "COMPLETE" : "LIVE";
        if (mode.equals("failed") && rollbackState()) return "CRITICAL";
        if (mode.equals("failed")) return "FAILED";
        if (mode.equals("rollback")) return "ROLLBACK";
        if (mode.equals("recovered")) return "RECOVERED";
        if (mode.equals("stopped")) return "STOPPED";
        return "LIVE";
    }

    String notificationTaskTitle() {
        String semantic = ui.notificationTitle(ui.operationTitle(""));
        if (!semantic.isEmpty()) return semantic;
        if (validationSession()) return task;
        if (rollbackState()) {
            if (succeeded()) return "Repository restored";
            if (failedState()) return "Rollback failed";
            return "Rolling back changes";
        }
        if (succeeded()) {
            String lowerTask = task.toLowerCase(Locale.ROOT);
            if (lowerTask.contains("overlay")) return "Overlay applied successfully";
            return "Devtool completed";
        }
        if (failedState()) {
            String lowerPhase = phase.toLowerCase(Locale.ROOT);
            if (lowerPhase.contains("build")) return "Build failed";
            if (lowerPhase.contains("test")) return "Tests failed";
            if (lowerPhase.contains("valid")) return "Validation failed";
            return "Devtool failed";
        }
        return taskTitle();
    }

    String notificationProgressLabel() {
        String mode = notificationMode();
        if (mode.equals("rollback")) return "ROLLBACK";
        if (mode.equals("recovered")) return "RESTORED";
        if (mode.equals("failed") && rollbackState()) return "CRITICAL";
        if (mode.equals("stopped")) return "STOPPED";
        if (current >= 0 && total > 0) return current + "/" + total;
        return displayProgress() + "%";
    }

    String notificationCompactStatus() {
        String semantic = ui.notificationBody("");
        if (!semantic.isEmpty()) return semantic;
        if (notificationMode().equals("recovered")) return scopeText() + " · Operation failed · all changes reverted";
        if (failedState() && rollbackState()) return scopeText() + " · Manual recovery may be required";
        if (failedState()) return scopeText() + " · " + phaseLabel() + " failed";
        if (isTerminal() && succeeded()) {
            int total = Math.max(workspaceTotal, workspaces.size());
            return total > 1 ? "All " + total + " workspaces completed" : scopeText() + " · Completed";
        }
        return scopeText() + " · " + localProgressText();
    }

    String notificationStatusLead() {
        if (validationSession()) {
            ValidationSessionRecord session = ValidationNotificationContext.lookup(validationSessionId());
            if (session != null) {
                if (session.blocked()) return "Blocked";
                if (session.state.equals("validated-uncommitted")) return "Commit pending";
                if (session.validated()) return "Validated";
                if (session.effectiveState.equals("pausing")) return "Pausing";
                if (session.effectiveState.equals("stopping")) return "Stopping";
                if (session.paused()) return "Paused";
            }
        }
        String mode = notificationMode();
        if (mode.equals("failed") && rollbackState()) return "Critical";
        if (mode.equals("failed")) return "Failure";
        if (mode.equals("rollback")) return "Rollback";
        if (mode.equals("recovered")) return "Recovered";
        if (mode.equals("success") || (mode.equals("warning") && isTerminal())) return "Complete";
        if (mode.equals("stopped")) return "Stopped";
        int total = Math.max(workspaceTotal, workspaces.size());
        if (total > 1) {
            int index = Math.max(1, Math.min(total, workspaceIndex > 0 ? workspaceIndex : 1));
            return "Workspace " + index + " of " + total;
        }
        return phaseLabel();
    }

    String notificationStatusDetail() {
        String semantic = ui.operationDetail("");
        if (!semantic.isEmpty()) return semantic;
        String mode = notificationMode();
        if (mode.equals("recovered")) return "Failure contained; repository restored";
        if (mode.equals("failed") && rollbackState()) return "Operation and rollback both failed";
        if (mode.equals("failed")) return firstUseful(detail, summary, "", "Validation stopped; rollback pending");
        if (mode.equals("rollback")) return "Reverting all applied workspace changes";
        if (mode.equals("success") || (mode.equals("warning") && isTerminal())) return completionDetail();
        if (mode.equals("stopped")) return firstUseful(detail, summary, "", "Run stopped before completion");
        return firstUseful(detail, localProgressText(), "", "Waiting for the next update");
    }

    String notificationStatusLine() {
        return notificationStatusLead() + " · " + notificationStatusDetail();
    }

    private String completionDetail() {
        int tests = passed + failed + skipped;
        if (tests > 0) {
            StringBuilder text = new StringBuilder()
                    .append(passed).append(" passed · ").append(failed).append(" failed");
            if (skipped > 0) text.append(" · ").append(skipped).append(" skipped");
            if (warnings > 0) text.append(" · ").append(warnings).append(" warnings");
            if (errors > 0) text.append(" · ").append(errors).append(" errors");
            return text.toString();
        }
        int total = Math.max(workspaceTotal, workspaces.size());
        if (total > 1) return "All " + total + " workspaces and validators passed";
        return firstUseful(detail, summary, "", "All validators passed");
    }

    Workspace activeWorkspace() {
        for (Workspace item : workspaces) if (item.active()) return item;
        if (!workspaces.isEmpty()) {
            int index = Math.max(0, Math.min(workspaces.size() - 1, Math.max(1, workspaceIndex) - 1));
            return workspaces.get(index);
        }
        return new Workspace(scopeText(), failedState() ? "failed" : succeeded() ? "done" : "active",
                isTerminal() ? detail : localProgressText(), displayProgress());
    }

    String remainingWorkspacesText() {
        if (workspaces.size() <= 1) {
            int remaining = Math.max(0, workspaceTotal - Math.max(1, workspaceIndex));
            return remaining > 0 ? remaining + " more workspace" + (remaining == 1 ? "" : "s") + " queued" : "";
        }
        List<String> names = new ArrayList<>();
        Workspace active = activeWorkspace();
        for (Workspace item : workspaces) {
            if (item == active) continue;
            if (names.size() >= 2) break;
            names.add(item.name + (item.done() ? " complete" : item.failed() ? " failed" : " waiting"));
        }
        int extra = Math.max(0, workspaces.size() - 1 - names.size());
        String text = String.join(" · ", names);
        return extra > 0 ? text + " · +" + extra + " more" : text;
    }

    String artifactLabel() {
        return artifact.isEmpty() ? runnerLabel() + " · " + environmentLabel() : shortName(artifact);
    }

    String workspacePositionText() {
        int total = Math.max(1, workspaceTotal > 0 ? workspaceTotal : workspaces.size());
        int index = Math.max(1, Math.min(total, workspaceIndex > 0 ? workspaceIndex : 1));
        return "Workspace " + index + " of " + total;
    }

    String testsMetricText() {
        int tests = passed + failed + skipped;
        if (tests <= 0 && testAssemblies <= 0) return "—";
        if (failed > 0) return failed + " failed";
        return passed + " passed";
    }

    String runnerLabel() {
        if (projectType.equals("dotnet")) return ".NET";
        if (projectType.equals("android")) return "Android";
        if (projectType.equals("gradle")) return "Gradle/JVM";
        if (projectType.equals("python")) return "Python";
        if (projectType.equals("command")) return "Custom command";
        return "Auto-detected runner";
    }

    String environmentLabel() {
        if (executionEnvironment.equals("native-termux")) return "Termux";
        if (executionEnvironment.equals("chroot")) return "Chroot";
        if (executionEnvironment.equals("desktop-linux")) return "Desktop Linux";
        if (executionEnvironment.equals("android-device")) return "Android device";
        if (executionEnvironment.equals("external-device")) return "External device";
        return "Automatic environment";
    }

    String projectUnitLabel() {
        if (projectType.equals("dotnet")) return "Projects";
        if (projectType.equals("android") || projectType.equals("gradle")) return "Gradle tasks";
        if (projectType.equals("python")) return "Python tasks";
        return "Tasks";
    }

    String testContainerLabel() {
        if (projectType.equals("dotnet")) return "Test assemblies";
        if (projectType.equals("android") || projectType.equals("gradle")) return "Test suites";
        if (projectType.equals("python")) return "Test files";
        return "Test groups";
    }

    String scopeText() {
        if (!workspace.isEmpty()) return workspace;
        if (!target.isEmpty()) return target;
        return repo;
    }

    String localProgressText() {
        String phasePosition = phaseTotal > 0 ? " " + Math.max(1, phaseIndex) + "/" + phaseTotal : "";
        if (current >= 0 && total > 0) {
            String noun = unitLabel.isEmpty() ? "items" : unitLabel;
            return phaseLabel() + phasePosition + " · " + current + "/" + total + " " + noun;
        }
        if (phaseProgress > 0) return phaseLabel() + phasePosition + " · " + phaseProgress + "%";
        return detail;
    }

    String elapsedText() {
        long elapsed = durationMs > 0 ? durationMs : Math.max(0L, System.currentTimeMillis() - startedAtMs);
        long seconds = elapsed / 1000L;
        return String.format(Locale.ROOT, "%02d:%02d", seconds / 60L, seconds % 60L);
    }


    private static String humanPhase(String phase) {
        String value = clean(phase, "Working");
        String lower = value.toLowerCase(Locale.ROOT).replace('_', '-');
        if (lower.equals("dotnet-validation")) return ".NET validation";
        if (lower.equals("android-validation")) return "Android validation";
        if (lower.equals("gradle-validation")) return "Gradle validation";
        return value;
    }

    private static String humanTask(String phase) {
        String value = clean(phase, "Working");
        String lower = value.toLowerCase(Locale.ROOT);
        if (lower.contains("extract")) return "Applying overlay";
        if (lower.contains("restore")) return "Restoring dependencies";
        if (lower.contains("build")) return "Building target";
        if (lower.contains("test")) return "Running tests";
        if (lower.contains("lint")) return "Running lint";
        if (lower.contains("validat")) return "Validating artifact";
        if (lower.contains("commit")) return "Creating commit";
        if (lower.contains("rollback")) return "Rolling back changes";
        return value;
    }

    private static String shortName(String value) {
        String clean = clean(value, "");
        int slash = Math.max(clean.lastIndexOf('/'), clean.lastIndexOf('\\'));
        return slash >= 0 ? clean.substring(slash + 1) : clean;
    }

    private static String normalizeProjectType(String value) {
        String result = clean(value, "auto").toLowerCase(Locale.ROOT).replace('_', '-');
        if (result.equals("jvm") || result.equals("jvm-gradle")) return "gradle";
        return result;
    }

    private static String normalizeExecutionEnvironment(String value) {
        return clean(value, "auto").toLowerCase(Locale.ROOT).replace('_', '-');
    }

    private static String normalizeStatus(String value) {
        String status = clean(value, "running").toLowerCase(Locale.ROOT).replace('_', '-');
        if (status.equals("success") || status.equals("passed") || status.equals("complete")) return "ok";
        if (status.equals("error") || status.equals("failure")) return "failed";
        return status;
    }

    private static String safeRunId(String value) {
        String id = clean(value, "default").replaceAll("[^A-Za-z0-9._-]", "_");
        return id.length() > 80 ? id.substring(0, 80) : id;
    }

    private static int clamp(int value) { return Math.max(0, Math.min(100, value)); }

    private static String clean(String value, String fallback) {
        if (value == null) return fallback;
        String cleaned = value.trim().replace('\n', ' ').replace('\r', ' ');
        return cleaned.isEmpty() ? fallback : cleaned;
    }

    static String plainText(String value) {
        if (value == null) return "";
        String cleaned = value.replaceAll("\\u001B\\[[;\\d]*[ -/]*[@-~]", "");
        cleaned = cleaned.replace('\r', ' ').replace('\n', ' ');
        cleaned = cleaned.replaceAll("[\\p{Cntrl}&&[^\\t]]", " ");
        cleaned = cleaned.replaceAll("\\s+", " ").trim();
        return cleaned;
    }
}
