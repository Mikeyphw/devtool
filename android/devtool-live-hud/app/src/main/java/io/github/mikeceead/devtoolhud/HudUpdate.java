package io.github.mikeceead.devtoolhud;

import android.content.Intent;
import android.os.SystemClock;

import java.util.Locale;

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
    final long startedAtMs;
    final long durationMs;
    final boolean overlayRequested;

    private HudUpdate(
            String runId, String status, String phase, String detail,
            String repo, String target, String commit, String summary,
            int progress, boolean indeterminate,
            int passed, int failed, int skipped, int warnings, int errors,
            long startedAtMs, long durationMs, boolean overlayRequested) {
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
                intent.getLongExtra("started_at_ms", 0L),
                intent.getLongExtra("duration_ms", 0L),
                intent.getBooleanExtra("overlay", false));
    }

    static HudUpdate sample() {
        return new HudUpdate("setup-test", "running", "Testing", "Running 7 of 10 test projects",
                "sims3tools", "S3Atelier.Modern.sln", "", "", 72, false,
                160, 0, 1, 2, 0, System.currentTimeMillis() - 83000L, 0L, false);
    }

    static HudUpdate sampleOverlay() {
        return new HudUpdate("setup-overlay-test", "running", "Testing", "Running 7 of 10 test projects",
                "sims3tools", "S3Atelier.Modern.sln", "", "", 72, false,
                160, 0, 1, 2, 0, System.currentTimeMillis() - 83000L, 0L, true);
    }

    static HudUpdate commandStarted(int executionId, String command, String workdir, boolean overlay) {
        return new HudUpdate("termux-" + executionId, "running", "Termux",
                clean(command, "Running command"), "Termux", clean(workdir, "~/"), "", "",
                0, true, 0, 0, 0, 0, 0, System.currentTimeMillis(), 0L, overlay);
    }

    static HudUpdate commandFinished(int executionId, int exitCode, String stdout, String stderr,
                                     String error, boolean overlay) {
        boolean ok = exitCode == 0 && clean(error, "").isEmpty();
        String detail = firstUseful(error, stderr, stdout, ok ? "Command completed" : "Command failed");
        return new HudUpdate("termux-" + executionId, ok ? "ok" : "failed", "Termux",
                detail, "Termux", "exit " + exitCode, "", "", 100, false,
                0, ok ? 0 : 1, 0, 0, ok ? 0 : 1,
                System.currentTimeMillis(), 0L, overlay);
    }

    private static String firstUseful(String first, String second, String third, String fallback) {
        for (String value : new String[]{first, second, third}) {
            String clean = clean(value, "");
            if (!clean.isEmpty()) return clean.length() > 180 ? clean.substring(0, 177) + "…" : clean;
        }
        return fallback;
    }

    boolean isTerminal() {
        return status.equals("ok") || status.equals("failed") || status.equals("interrupted")
                || status.equals("cancelled") || status.equals("skipped");
    }

    boolean succeeded() { return status.equals("ok"); }
    boolean failedState() { return status.equals("failed"); }

    String title() {
        String operation = operationName();
        if (succeeded()) return operation + " completed";
        if (failedState()) return operation + " failed";
        if (status.equals("interrupted") || status.equals("cancelled")) return operation + " interrupted";
        if (status.equals("waiting")) return "Preparing · " + repo;
        return phase + " · " + repo;
    }

    private String operationName() {
        String lower = phase.toLowerCase(Locale.ROOT);
        if (lower.contains("test")) return "Tests";
        if (lower.contains("commit")) return "Commit";
        if (lower.contains("overlay") || lower.contains("apply")) return "Overlay";
        if (lower.contains("validat")) return "Validation";
        if (lower.contains("restor")) return "Restore";
        if (lower.contains("build")) return "Build";
        return "Devtool run";
    }

    String contentText() {
        if (isTerminal()) {
            int total = passed + failed + skipped;
            StringBuilder text = new StringBuilder();
            if (total > 0) text.append(passed).append(" passed · ").append(failed).append(" failed · ").append(skipped).append(" skipped");
            else text.append(detail);
            if (warnings > 0) text.append(" · ").append(warnings).append(" warning").append(warnings == 1 ? "" : "s");
            if (!commit.isEmpty()) text.append(" · ").append(commit);
            return text.toString();
        }
        return detail;
    }

    String subText() {
        return target.isEmpty() ? repo : repo + " · " + target;
    }

    String criticalText() {
        if (succeeded()) return "Done";
        if (failedState()) return "Failed";
        if (indeterminate) return phase;
        return progress + "%";
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
}
