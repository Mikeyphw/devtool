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
    final String status;
    final String phase;
    final String detail;
    final String repo;
    final String target;
    final String commit;
    final String summary;
    final int progress;
    final boolean indeterminate;
    final int passed;
    final int failed;
    final int skipped;
    final int warnings;
    final int errors;
    final List<Step> steps;
    final String stepsJson;
    final long startedAtMs;
    final long durationMs;
    final boolean overlayRequested;

    private HudUpdate(
            String runId, String status, String phase, String detail,
            String repo, String target, String commit, String summary,
            int progress, boolean indeterminate,
            int passed, int failed, int skipped, int warnings, int errors,
            String stepsJson, long startedAtMs, long durationMs, boolean overlayRequested) {
        this.runId = safeRunId(runId);
        this.status = normalizeStatus(status);
        this.phase = clean(phase, "Working");
        this.detail = clean(detail, "Devtool task in progress");
        this.repo = clean(repo, "devtool");
        this.target = clean(target, "");
        this.commit = clean(commit, "");
        this.summary = clean(summary, "");
        this.progress = Math.max(0, Math.min(100, progress));
        this.indeterminate = indeterminate;
        this.passed = Math.max(0, passed);
        this.failed = Math.max(0, failed);
        this.skipped = Math.max(0, skipped);
        this.warnings = Math.max(0, warnings);
        this.errors = Math.max(0, errors);
        this.stepsJson = clean(stepsJson, "");
        this.steps = parseSteps(this.stepsJson, this.phase, this.status, this.progress);
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
        boolean indeterminate = intent.getBooleanExtra("indeterminate", percent < 0);
        return new HudUpdate(
                intent.getStringExtra("run_id"),
                intent.getStringExtra("status"),
                intent.getStringExtra("phase"),
                intent.getStringExtra("detail"),
                intent.getStringExtra("repo"),
                intent.getStringExtra("target"),
                intent.getStringExtra("commit"),
                intent.getStringExtra("summary"),
                Math.max(0, percent), indeterminate,
                intent.getIntExtra("passed", 0),
                intent.getIntExtra("failed", 0),
                intent.getIntExtra("skipped", 0),
                intent.getIntExtra("warnings", 0),
                intent.getIntExtra("errors", 0),
                intent.getStringExtra("steps_json"),
                intent.getLongExtra("started_at_ms", 0L),
                intent.getLongExtra("duration_ms", 0L),
                intent.getBooleanExtra("overlay", false));
    }

    static HudUpdate sample() {
        return new HudUpdate("setup-test", "running", "Testing", "Running 7 of 10 test projects",
                "sims3tools", "S3Atelier.Modern.sln", "", "", 72, false,
                160, 0, 1, 2, 0, sampleSteps(), System.currentTimeMillis() - 83000L, 0L, false);
    }

    static HudUpdate sampleOverlay() {
        return new HudUpdate("setup-overlay-test", "running", "Testing", "Running 7 of 10 test projects",
                "sims3tools", "S3Atelier.Modern.sln", "", "", 72, false,
                160, 0, 1, 2, 0, sampleSteps(), System.currentTimeMillis() - 83000L, 0L, true);
    }

    static HudUpdate commandStarted(String runId, String command, String workdir, boolean overlay) {
        return new HudUpdate(runId, "running", "Preparing",
                "Waiting for devtool progress", "Termux", clean(workdir, "~/"), "", clean(command, ""),
                0, true, 0, 0, 0, 0, 0, "", System.currentTimeMillis(), 0L, overlay);
    }

    static HudUpdate commandFinished(String runId, int exitCode, String stdout, String stderr,
                                     String error, boolean overlay) {
        boolean ok = exitCode == 0 && clean(error, "").isEmpty();
        String detail = firstUseful(error, stderr, stdout, ok ? "Command completed" : "Command failed");
        return new HudUpdate(runId, ok ? "ok" : "failed", "Termux",
                detail, "Termux", "exit " + exitCode, "", "", 100, false,
                0, ok ? 0 : 1, 0, 0, ok ? 0 : 1, "",
                System.currentTimeMillis(), 0L, overlay);
    }

    private static String firstUseful(String first, String second, String third, String fallback) {
        for (String value : new String[]{first, second, third}) {
            String cleaned = plainText(clean(value, ""));
            if (!cleaned.isEmpty()) return cleaned.length() > 180 ? cleaned.substring(0, 177) + "…" : cleaned;
        }
        return fallback;
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
            this.progress = Math.max(0, Math.min(100, progress));
        }

        boolean running() { return status.equals("running"); }
        boolean succeeded() { return status.equals("ok"); }
        boolean failed() { return status.equals("failed"); }
        boolean skipped() { return status.equals("skipped"); }
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
                + "{\"key\":\"restore\",\"label\":\"Restore\",\"status\":\"ok\",\"progress\":100},"
                + "{\"key\":\"build\",\"label\":\"Build\",\"status\":\"ok\",\"progress\":100},"
                + "{\"key\":\"test\",\"label\":\"Test\",\"status\":\"running\",\"progress\":72},"
                + "{\"key\":\"commit\",\"label\":\"Commit\",\"status\":\"pending\",\"progress\":0}]";
    }

    boolean isTerminal() {
        return status.equals("ok") || status.equals("failed") || status.equals("interrupted")
                || status.equals("cancelled") || status.equals("skipped");
    }

    boolean succeeded() { return status.equals("ok"); }
    boolean failedState() { return status.equals("failed"); }

    int displayProgress() {
        if (succeeded()) return 100;
        // Overall pipeline progress is supplied by the producer. The active
        // step retains an independent local percentage for expanded text.
        return progress;
    }

    int activeStepProgress() {
        Step active = activeStep();
        return active == null ? progress : active.progress;
    }

    String phaseLabel() {
        if (succeeded()) return "Completed";
        if (failedState()) return "Failed";
        if (status.equals("interrupted") || status.equals("cancelled")) return "Interrupted";
        if (status.equals("waiting")) return "Preparing";
        return phase;
    }

    String title() {
        return "Devtool · " + phaseLabel();
    }

    String contentText() {
        String scope = target.isEmpty() ? repo : target;
        if (isTerminal()) {
            int total = passed + failed + skipped;
            if (total > 0) return scope + " · " + passed + " passed · " + failed + " failed";
            return scope + " · " + detail;
        }
        Step active = activeStep();
        String local = detail;
        if (active != null && active.progress > 0 && !detail.matches(".*\\d+\\s*/\\s*\\d+.*")) {
            local = active.label + " " + active.progress + "%";
        }
        return scope + " · " + local;
    }

    String subText() {
        StringBuilder text = new StringBuilder();
        text.append(displayProgress()).append("% · ").append(elapsedText());
        if (errors > 0) text.append(" · ").append(errors).append(" error").append(errors == 1 ? "" : "s");
        else if (warnings > 0) text.append(" · ").append(warnings).append(" warning").append(warnings == 1 ? "" : "s");
        else text.append(" · healthy");
        return text.toString();
    }

    String expandedText() {
        StringBuilder text = new StringBuilder();
        text.append(target.isEmpty() ? repo : target).append("\n");
        for (Step step : steps) {
            String mark = step.succeeded() ? "✓" : step.failed() ? "✕" : step.running() ? "●" : "○";
            text.append(mark).append(" ").append(step.label);
            if (step.running() && step.progress > 0) text.append(" · ").append(step.progress).append("%");
            text.append("\n");
        }
        int testTotal = passed + failed + skipped;
        if (testTotal > 0) {
            text.append("\nTests  ").append(passed).append(" passed · ").append(failed)
                    .append(" failed · ").append(skipped).append(" skipped");
        }
        text.append("\nHealth  ").append(warnings).append(" warnings · ").append(errors).append(" errors");
        text.append("\nElapsed  ").append(elapsedText());
        return text.toString();
    }

    String copySummaryText() {
        if (!summary.isEmpty()) return summary;
        return title() + "\n" + contentText() + "\n" + subText() + "\n\n" + expandedText();
    }

    private String elapsedText() {
        long elapsed = durationMs > 0 ? durationMs : Math.max(0L, System.currentTimeMillis() - startedAtMs);
        long seconds = elapsed / 1000L;
        return String.format(Locale.ROOT, "%02d:%02d", seconds / 60L, seconds % 60L);
    }

    Step activeStep() {
        for (Step step : steps) if (step.running()) return step;
        return null;
    }

    int pipelineProgress() {
        if (steps.isEmpty()) return progress;
        double total = 0.0;
        for (Step step : steps) {
            if (step.succeeded() || step.failed() || step.skipped()) total += 100.0;
            else total += step.progress;
        }
        return Math.max(0, Math.min(100, (int) Math.round(total / steps.size())));
    }

    String criticalText() {
        if (succeeded()) return "Done";
        if (failedState()) return "Failed";
        Step active = activeStep();
        if (active != null) return active.label;
        if (indeterminate) return phase;
        return displayProgress() + "%";
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
