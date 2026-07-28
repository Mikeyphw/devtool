package io.github.mikeceead.devtoolhud;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.WindowInsets;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

public final class ValidationEvidenceActivity extends Activity {
    static final String EXTRA_SESSION_ID = "validation_session_id";
    static final String EXTRA_PHASE_KEY = "validation_phase_key";
    private UiKit ui;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        ui = new UiKit(this);
        getWindow().setStatusBarColor(ui.palette.background);
        getWindow().setNavigationBarColor(ui.palette.background);
        setContentView(build());
    }

    private View build() {
        Intent intent = getIntent();
        String sessionId = intent == null ? "" : clean(intent.getStringExtra(EXTRA_SESSION_ID));
        String phaseKey = intent == null ? "" : clean(intent.getStringExtra(EXTRA_PHASE_KEY));
        ValidationSessionRecord session = ValidationStore.find(this, sessionId);
        ValidationPhaseRecord phase = findPhase(session, phaseKey);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(ui.palette.background);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                view.setPadding(0, bars.top, 0, bars.bottom);
            }
            return insets;
        });
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(ui.dp(18), ui.dp(16), ui.dp(18), ui.dp(10));
        header.addView(ui.text(phase == null ? "Validation report" : phase.label,
                UiTypography.Role.PAGE_TITLE, ui.palette.text));
        header.addView(ui.text(session == null ? "Session unavailable" : session.title() + " · " + session.cleanExecutorKind(),
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        root.addView(header);

        ScrollView scroll = new ScrollView(this);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(ui.contentHorizontalPadding(), ui.dp(4), ui.contentHorizontalPadding(), ui.dp(24));
        if (session == null) {
            LinearLayout card = ui.surface();
            card.addView(ui.text("Report is no longer cached", UiTypography.Role.CARD_TITLE, ui.palette.text));
            card.addView(ui.text("Refresh the validation session and open the report again.", UiTypography.Role.BODY, ui.palette.muted));
            content.addView(card);
        } else if (phase == null) {
            addEvidence(content, "Final verdict", first(session.finalVerdict, session.state));
            addEvidence(content, "Report path", session.reportPath);
            addEvidence(content, "Repository", session.repoRoot);
            addEvidence(content, "Target", session.target);
            addEvidence(content, "Git heads", "Initial: " + session.initialGitHead + "\nFinal: " + session.finalGitHead);
            addEvidence(content, "Commit", session.commitStatus + (session.commitHash.isEmpty() ? "" : "\n" + session.commitHash));
            addEvidence(content, "Diagnostics", session.reportErrors + " errors\n" + session.reportWarnings + " warnings");
            addEvidence(content, "Rollback", "State: " + session.rollbackStatus + "\nVerified: "
                    + session.rollbackVerifiedPaths + "/" + session.rollbackTotalPaths
                    + (session.rollbackError.isEmpty() ? "" : "\nError: " + session.rollbackError));
            for (ValidationPhaseRecord item : session.phases) {
                addEvidence(content, item.index + "/" + item.total + " · " + item.label, phaseSummary(item));
            }
            LinearLayout actions = ui.surface();
            actions.addView(ui.sectionTitle("Report actions"));
            actions.addView(ui.secondaryButton("Copy validation report", v -> copy("Validation report", report(session, null))));
            actions.addView(ui.secondaryButton("Copy report path", v -> copy("Validation report path", session.reportPath)));
            content.addView(actions);
        } else {
            addEvidence(content, "Command", commandText(phase.commandJson));
            addEvidence(content, "Log path", phase.logPath);
            addEvidence(content, "Failure", phase.failureSummary);
            addEvidence(content, "Issues", pretty(phase.issuesJson));
            addEvidence(content, "Classifier evidence", pretty(phase.classificationEvidenceJson));
            addEvidence(content, "Supervisor events", pretty(phase.supervisorEventsJson));
            addEvidence(content, "Inputs before", pretty(phase.inputSnapshotBeforeJson));
            addEvidence(content, "Inputs after", pretty(phase.inputSnapshotAfterJson));
            if (!phase.attemptHistory.isEmpty()) {
                for (ValidationAttemptRecord attempt : phase.attemptHistory) {
                    String title = "Attempt " + (attempt.attemptId.isEmpty() ? "evidence" : attempt.attemptId);
                    addEvidence(content, title, attemptText(attempt));
                }
            }
            LinearLayout actions = ui.surface();
            actions.addView(ui.sectionTitle("Evidence actions"));
            actions.addView(ui.secondaryButton("Copy command", v -> copy("Validation command", commandText(phase.commandJson))));
            actions.addView(ui.secondaryButton("Copy log path", v -> copy("Validation log path", phase.logPath)));
            actions.addView(ui.secondaryButton("Copy evidence report", v -> copy("Validation evidence", report(session, phase))));
            content.addView(actions);
        }
        scroll.addView(content);
        root.addView(scroll, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        return root;
    }

    private void addEvidence(LinearLayout content, String title, String value) {
        if (value == null || value.trim().isEmpty() || value.equals("[]") || value.equals("{}")) return;
        LinearLayout card = ui.surface();
        card.addView(ui.text(title, UiTypography.Role.CARD_TITLE, ui.palette.text));
        TextView body = ui.text(value, UiTypography.Role.BODY_SMALL, ui.palette.text);
        body.setTypeface(Typeface.MONOSPACE);
        body.setTextIsSelectable(true);
        card.addView(body);
        content.addView(card);
    }

    private static ValidationPhaseRecord findPhase(ValidationSessionRecord session, String key) {
        if (session == null) return null;
        for (ValidationPhaseRecord phase : session.phases) if (phase.key.equals(key) || phase.name.equals(key)) return phase;
        return null;
    }

    static String commandText(String raw) {
        try {
            JSONArray values = new JSONArray(raw == null ? "[]" : raw);
            StringBuilder result = new StringBuilder();
            for (int i = 0; i < values.length(); i++) {
                if (result.length() > 0) result.append(' ');
                String value = values.optString(i, "");
                result.append(shell(value));
            }
            return result.toString();
        } catch (JSONException ignored) {
            return clean(raw);
        }
    }

    private static String attemptText(ValidationAttemptRecord attempt) {
        StringBuilder value = new StringBuilder();
        value.append("Verdict: ").append(attempt.verdict).append('\n');
        if (!attempt.category.isEmpty()) value.append("Category: ").append(attempt.category).append('\n');
        if (attempt.returnCode >= 0) value.append("Exit: ").append(attempt.returnCode).append('\n');
        if (attempt.signal > 0) value.append("Signal: ").append(attempt.signal).append('\n');
        if (!attempt.resourceProfile.isEmpty()) value.append("Resource profile: ").append(attempt.resourceProfile).append('\n');
        if (!attempt.logPath.isEmpty()) value.append("Log: ").append(attempt.logPath).append('\n');
        if (!attempt.outputTail.isEmpty()) value.append("\nOutput tail:\n").append(attempt.outputTail).append('\n');
        if (!attempt.issuesJson.equals("[]")) value.append("\nIssues:\n").append(pretty(attempt.issuesJson)).append('\n');
        if (!attempt.classificationEvidenceJson.equals("[]")) value.append("\nClassifier evidence:\n").append(pretty(attempt.classificationEvidenceJson)).append('\n');
        if (!attempt.supervisorEventsJson.equals("[]")) value.append("\nSupervisor events:\n").append(pretty(attempt.supervisorEventsJson));
        return value.toString().trim();
    }

    private static String report(ValidationSessionRecord session, ValidationPhaseRecord phase) {
        StringBuilder value = new StringBuilder();
        value.append(session.title()).append(" validation\n")
                .append("Verdict: ").append(first(session.finalVerdict, session.state)).append('\n')
                .append("Repository: ").append(session.repositoryId).append('\n')
                .append("Path: ").append(session.repoRoot).append('\n')
                .append("Executor: ").append(session.cleanExecutorKind())
                .append(session.executorName.isEmpty() ? "" : "/" + session.executorName).append('\n')
                .append("Phases: ").append(session.completedPhases).append('/').append(session.phaseTotal).append('\n')
                .append("Diagnostics: ").append(session.reportErrors).append(" errors, ")
                .append(session.reportWarnings).append(" warnings\n")
                .append("Commit: ").append(session.commitStatus)
                .append(session.commitHash.isEmpty() ? "" : " " + session.commitHash).append('\n')
                .append("Report: ").append(session.reportPath);
        if (phase != null) value.append("\nPhase: ").append(phase.label).append("\nStatus: ").append(phase.status)
                .append("\nCommand: ").append(commandText(phase.commandJson))
                .append("\nLog: ").append(phase.logPath).append("\nFailure: ").append(phase.failureSummary);
        return value.toString();
    }

    private static String phaseSummary(ValidationPhaseRecord phase) {
        StringBuilder value = new StringBuilder();
        value.append("Status: ").append(phase.status).append("\nAttempts: ").append(phase.attemptHistory.size());
        if (!phase.lastVerdict.isEmpty()) value.append("\nVerdict: ").append(phase.lastVerdict);
        if (!phase.invalidatedReason.isEmpty()) value.append("\nReason: ").append(phase.invalidatedReason);
        if (!phase.logPath.isEmpty()) value.append("\nLog: ").append(phase.logPath);
        return value.toString();
    }

    private void copy(String label, String value) {
        if (value == null || value.trim().isEmpty()) {
            Toast.makeText(this, "No evidence is available to copy", Toast.LENGTH_SHORT).show();
            return;
        }
        ClipboardManager clipboard = getSystemService(ClipboardManager.class);
        if (clipboard == null) {
            Toast.makeText(this, "Clipboard is unavailable", Toast.LENGTH_LONG).show();
            return;
        }
        clipboard.setPrimaryClip(ClipData.newPlainText(label, value));
        Toast.makeText(this, label + " copied", Toast.LENGTH_SHORT).show();
    }

    private static String pretty(String raw) {
        String clean = clean(raw);
        try {
            if (clean.startsWith("[")) return new JSONArray(clean).toString(2);
            if (clean.startsWith("{")) return new JSONObject(clean).toString(2);
        } catch (JSONException ignored) {}
        return clean;
    }

    private static String shell(String value) {
        String clean = value == null ? "" : value;
        if (clean.matches("[A-Za-z0-9_./:=+@%-]+")) return clean;
        return "'" + clean.replace("'", "'\\''") + "'";
    }

    private static String first(String... values) { for (String value : values) if (value != null && !value.trim().isEmpty()) return value.trim(); return ""; }

    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
