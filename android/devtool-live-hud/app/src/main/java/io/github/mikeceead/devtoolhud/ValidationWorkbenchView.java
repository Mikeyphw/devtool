package io.github.mikeceead.devtoolhud;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Typeface;
import android.view.Gravity;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONException;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

final class ValidationWorkbenchView {
    private final Activity activity;
    private final UiKit ui;
    private final Runnable refresh;
    private ValidationSessionRecord session;
    private final ValidationScope scope = new ValidationScope();
    private Spinner scopeSpinner;
    private Spinner throughSpinner;
    private LinearLayout selectedList;
    private LinearLayout phaseDetailPane;
    private final Map<String, CheckBox> phaseChecks = new HashMap<>();
    private final Set<String> automaticDependencies = new HashSet<>();
    private boolean updatingSelection;

    ValidationWorkbenchView(Activity activity, UiKit ui, ValidationSessionRecord session, Runnable refresh) {
        this.activity = activity;
        this.ui = ui;
        this.session = session;
        this.refresh = refresh;
        restoreScope();
    }

    View buildPage(String fallbackRepo) {
        LinearLayout content = new LinearLayout(activity);
        content.setOrientation(LinearLayout.VERTICAL);
        if (session == null) {
            LinearLayout empty = ui.surface();
            empty.addView(ui.sectionTitle("Validation Workbench"));
            empty.addView(ui.text("No validation session is cached", UiTypography.Role.LARGE_VALUE, ui.palette.text));
            empty.addView(ui.text("Ask Devtool for the active durable session. The Workbench never invents phase state locally.",
                    UiTypography.Role.BODY, ui.palette.muted));
            empty.addView(ui.button("Find active validation", ui.palette.cyan, v -> {
                TermuxCommandRunner.LaunchResult result = ValidationController.refresh(activity, fallbackRepo, "", false);
                ValidationController.showLaunchResult(activity, result, "Looking for an active validation session");
            }));
            content.addView(empty);
            return content;
        }
        content.addView(sessionHeader());
        content.addView(scopeCard());
        if (ui.wideLayout()) {
            LinearLayout split = new LinearLayout(activity);
            split.setOrientation(LinearLayout.HORIZONTAL);
            View timeline = timelineCard();
            phaseDetailPane = ui.surface();
            ValidationPhaseRecord initial = session.activePhase();
            if (initial == null) initial = session.failedPhase();
            if (initial == null) initial = session.nextPhase();
            renderPhaseDetailPane(initial);
            split.addView(timeline, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            LinearLayout.LayoutParams detailParams = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
            detailParams.setMargins(ui.dp(10), 0, 0, 0);
            split.addView(phaseDetailPane, detailParams);
            content.addView(split);
        } else {
            content.addView(timelineCard());
        }
        content.addView(sessionOptionsCard());
        return content;
    }

    View buildActionBar() {
        LinearLayout bar = new LinearLayout(activity);
        bar.setOrientation(ui.verticalActions() ? LinearLayout.VERTICAL : LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(ui.dp(14), ui.dp(8), ui.dp(14), ui.dp(10));
        bar.setBackgroundColor(ui.palette.surface);
        if (session == null) return bar;
        if (session.recoveryRequired()) {
            addAction(bar, "Recover session", false, v -> launch(ValidationController.recover(activity, session), "Recovery requested"));
            addAction(bar, "Review state", false, v -> showPhaseDetail(session.failedPhase() != null ? session.failedPhase() : session.activePhase()));
        } else if (session.effectiveState.equals("pausing")) {
            addAction(bar, "Keep running", false, v -> launch(ValidationController.cancelPause(activity, session), "Pause cancelled"));
            addAction(bar, "Stop now", true, v -> confirmStop());
        } else if (session.effectiveState.equals("stopping")) {
            addAction(bar, "Stopping safely…", false, v -> {});
        } else if (session.running()) {
            addAction(bar, "Pause after phase", false, v -> launch(ValidationController.pause(activity, session), "Pause requested"));
            addAction(bar, "Stop now", true, v -> confirmStop());
        } else if (session.blocked()) {
            addAction(bar, "Review failure", false, v -> showPhaseDetail(session.failedPhase()));
            if (!session.recommendedRetryProfile.isEmpty()) {
                addAction(bar, "Retry safely", false, v -> launch(ValidationController.retry(activity, session, true), "Safe retry requested"));
            } else {
                addAction(bar, "Retry phase", false, v -> launch(ValidationController.retry(activity, session, false), "Retry requested"));
            }
        } else if (session.validated()) {
            addAction(bar, session.state.equals("validated-uncommitted") ? "Retry commit" : "Finalize", false,
                    v -> launch(ValidationController.finalizeSession(activity, session), "Finalization requested"));
        } else if (session.paused() && !session.recommendedRetryProfile.isEmpty()) {
            addAction(bar, "Retry safely", false, v -> launch(ValidationController.retry(activity, session, true), "Safe retry requested"));
            addAction(bar, "Change scope", false, v -> launchRun());
        } else {
            addAction(bar, session.paused() ? "Start validation" : "Resume", false, v -> launchRun());
        }
        return bar;
    }

    private View sessionHeader() {
        LinearLayout card = ui.surface();
        LinearLayout titleRow = new LinearLayout(activity);
        titleRow.setOrientation(LinearLayout.HORIZONTAL);
        titleRow.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout copy = new LinearLayout(activity);
        copy.setOrientation(LinearLayout.VERTICAL);
        TextView title = ui.text(session.title(), UiTypography.Role.LARGE_VALUE, ui.palette.text);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        copy.addView(title);
        copy.addView(ui.text(session.validationMode.replace('-', ' ') + " validation",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        titleRow.addView(copy, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        TextView badge = ui.text(stateLabel(), UiTypography.Role.CAPTION, stateColor());
        badge.setPadding(ui.dp(9), ui.dp(6), ui.dp(9), ui.dp(6));
        badge.setBackground(ui.flat(UiKit.withAlpha(stateColor(), 30), 999));
        titleRow.addView(badge);
        card.addView(titleRow);

        ProgressBar progress = new ProgressBar(activity, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        progress.setProgress(session.overallProgress());
        progress.setIndeterminate(false);
        progress.setContentDescription("Validation progress " + session.completedPhases + " of " + session.phaseTotal + " phases");
        LinearLayout.LayoutParams progressParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, ui.dp(8));
        progressParams.setMargins(0, ui.dp(15), 0, ui.dp(9));
        card.addView(progress, progressParams);
        card.addView(ui.text(session.completedPhases + " of " + session.phaseTotal + " phases complete",
                UiTypography.Role.BODY, ui.palette.text));
        String detail = headerDetail();
        card.addView(ui.text(detail, UiTypography.Role.BODY_SMALL, ui.palette.muted));
        String chips = session.recoveryAvailable ? "Deferred apply  ·  Rollback available" : "Standalone validation";
        chips += "  ·  " + humanEnvironment(session.executionEnvironment);
        card.addView(ui.text(chips, UiTypography.Role.CAPTION, session.recoveryAvailable ? ui.palette.green : ui.palette.muted));
        if (!session.lastError.isEmpty()) card.addView(ui.text(session.lastError, UiTypography.Role.BODY_SMALL, ui.palette.red));
        card.addView(ui.secondaryButton("Refresh session", v -> launch(
                ValidationController.refresh(activity, repo(), session.sessionId, session.executionEnvironment.contains("root")),
                "Refreshing validation")));
        return card;
    }

    private View scopeCard() {
        LinearLayout card = ui.surface();
        card.addView(ui.sectionTitle("Run scope"));
        card.addView(ui.text("Choose one safe slice. Required dependencies are included automatically by Devtool.",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        String[] modes = {"Next phase", "Through phase…", "Selected phases…", "All remaining"};
        scopeSpinner = spinner(modes, scopeIndex(scope.mode));
        card.addView(scopeSpinner);
        List<String> labels = new ArrayList<>();
        int selectedThrough = 0;
        for (int i = 0; i < session.phases.size(); i++) {
            ValidationPhaseRecord phase = session.phases.get(i);
            labels.add(phase.label);
            if (phase.key.equals(scope.throughPhase) || phase.name.equals(scope.throughPhase)) selectedThrough = i;
        }
        throughSpinner = spinner(labels.toArray(new String[0]), selectedThrough);
        card.addView(throughSpinner);
        selectedList = new LinearLayout(activity);
        selectedList.setOrientation(LinearLayout.VERTICAL);
        card.addView(selectedList);
        populateSelectedPhases();
        scopeSpinner.setOnItemSelectedListener(new SimpleItemListener(position -> {
            scope.mode = position == 1 ? ValidationScope.THROUGH : position == 2 ? ValidationScope.SELECTED
                    : position == 3 ? ValidationScope.REMAINING : ValidationScope.NEXT;
            updateScopeVisibility();
        }));
        throughSpinner.setOnItemSelectedListener(new SimpleItemListener(position -> {
            if (position >= 0 && position < session.phases.size()) scope.throughPhase = session.phases.get(position).key;
        }));
        updateScopeVisibility();
        TextView summary = ui.text("Scope is checked again by the CLI before execution.", UiTypography.Role.CAPTION, ui.palette.muted);
        summary.setPadding(0, ui.dp(8), 0, 0);
        card.addView(summary);
        return card;
    }

    private View timelineCard() {
        LinearLayout card = ui.surface();
        card.addView(ui.sectionTitle("Validation phases"));
        for (ValidationPhaseRecord phase : session.phases) card.addView(phaseRow(phase));
        return card;
    }

    private View phaseRow(ValidationPhaseRecord phase) {
        Button row = new Button(activity);
        String marker = phase.passed() ? "✓" : phase.failed() ? "!" : phase.running() ? "●" : phase.status.equals("stale") ? "↻" : "○";
        String detail = phase.status.replace('-', ' ');
        if (phase.running() && phase.progress > 0) detail += " · " + phase.progress + "%";
        if (phase.running() && !phase.progressDetail.isEmpty()) detail += " · " + phase.progressDetail;
        if (phase.durationMs > 0) detail += " · " + formatDuration(phase.durationMs);
        if (phase.attempts > 0) detail += " · attempt " + phase.attempts;
        row.setText(activity.getString(R.string.validation_phase_row_format, marker, phase.label, detail));
        row.setAllCaps(false);
        row.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
        row.setTextColor(phaseColor(phase));
        row.setBackground(ui.flat(ui.palette.surfaceRaised, 10));
        row.setPadding(ui.dp(12), ui.dp(10), ui.dp(12), ui.dp(10));
        row.setMinHeight(ui.dp(58));
        row.setContentDescription(phase.label + ". " + detail + ". Open phase details.");
        row.setOnClickListener(v -> {
            if (ui.wideLayout() && phaseDetailPane != null) renderPhaseDetailPane(phase);
            else showPhaseDetail(phase);
        });
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, ui.dp(4), 0, ui.dp(4));
        row.setLayoutParams(params);
        return row;
    }

    private View sessionOptionsCard() {
        LinearLayout card = ui.surface();
        card.addView(ui.sectionTitle("Session options"));
        if (session.recoveryRequired() || session.recoveryAvailable) {
            card.addView(ui.secondaryButton("Recover validation session", v -> launch(
                    ValidationController.recover(activity, session), "Recovery requested")));
        }
        card.addView(ui.secondaryButton("Restart from phase…", v -> chooseResetPhase(true)));
        card.addView(ui.secondaryButton("Reset validation progress", v -> confirmResetAll()));
        card.addView(ui.secondaryButton("Discard session and keep changes", v -> confirmAbort(false)));
        Button rollback = ui.secondaryButton("Roll back applied changes", v -> confirmAbort(true));
        rollback.setTextColor(ui.palette.red);
        rollback.setEnabled(session.recoveryAvailable);
        rollback.setAlpha(session.recoveryAvailable ? 1f : 0.5f);
        card.addView(rollback);
        card.addView(ui.text("Rollback is intentionally unavailable from notifications and requires confirmation here.",
                UiTypography.Role.CAPTION, ui.palette.muted));
        return card;
    }

    private void populateSelectedPhases() {
        selectedList.removeAllViews();
        phaseChecks.clear();
        for (ValidationPhaseRecord phase : session.phases) {
            CheckBox check = new CheckBox(activity);
            check.setTextColor(ui.palette.text);
            check.setMinHeight(ui.dp(48));
            check.setOnCheckedChangeListener((button, checked) -> {
                if (updatingSelection) return;
                if (checked && !scope.selectedPhases.contains(phase.key)) scope.selectedPhases.add(phase.key);
                if (!checked) scope.selectedPhases.remove(phase.key);
                updateSelectedDependencies();
            });
            phaseChecks.put(phase.key, check);
            selectedList.addView(check);
        }
        updateSelectedDependencies();
    }

    private void updateSelectedDependencies() {
        automaticDependencies.clear();
        for (String key : new ArrayList<>(scope.selectedPhases)) collectDependencies(key, automaticDependencies);
        automaticDependencies.removeAll(scope.selectedPhases);
        updatingSelection = true;
        try {
            for (ValidationPhaseRecord phase : session.phases) {
                CheckBox check = phaseChecks.get(phase.key);
                if (check == null) continue;
                boolean automatic = automaticDependencies.contains(phase.key);
                boolean selected = scope.selectedPhases.contains(phase.key);
                check.setChecked(selected || automatic);
                check.setEnabled(!phase.passed() && !automatic);
                String label = automatic
                        ? activity.getString(R.string.validation_phase_required_dependency, phase.label)
                        : (phase.required ? phase.label : activity.getString(R.string.validation_phase_optional, phase.label));
                check.setText(label);
                check.setContentDescription(phase.label + (automatic ? ", required dependency" : ", selectable phase"));
            }
        } finally {
            updatingSelection = false;
        }
    }

    private void collectDependencies(String key, Set<String> result) {
        ValidationPhaseRecord phase = phaseByKey(key);
        if (phase == null) return;
        try {
            JSONArray deps = new JSONArray(phase.dependenciesJson);
            for (int i = 0; i < deps.length(); i++) {
                String dependency = deps.optString(i, "");
                if (!dependency.isEmpty() && result.add(dependency)) collectDependencies(dependency, result);
            }
        } catch (JSONException ignored) {}
    }

    private ValidationPhaseRecord phaseByKey(String key) {
        for (ValidationPhaseRecord phase : session.phases) if (phase.key.equals(key) || phase.name.equals(key)) return phase;
        return null;
    }

    private void updateScopeVisibility() {
        throughSpinner.setVisibility(ValidationScope.THROUGH.equals(scope.mode) ? View.VISIBLE : View.GONE);
        selectedList.setVisibility(ValidationScope.SELECTED.equals(scope.mode) ? View.VISIBLE : View.GONE);
    }

    private void showPhaseDetail(ValidationPhaseRecord phase) {
        if (phase == null) {
            Toast.makeText(activity, "No phase evidence is available", Toast.LENGTH_SHORT).show();
            return;
        }
        AlertDialog.Builder dialog = new AlertDialog.Builder(activity)
                .setTitle(phase.label)
                .setMessage(phaseDetailText(phase).toString())
                .setNegativeButton("Close", null)
                .setNeutralButton("Open evidence", (d, w) -> openEvidence(phase));
        if (phase.failed() || phase.status.equals("interrupted") || phase.status.equals("stale")) {
            String label = session.recommendedRetryProfile.isEmpty() ? "Retry phase" : "Retry safely";
            dialog.setPositiveButton(label, (d, w) -> launch(
                    ValidationController.retry(activity, session, !session.recommendedRetryProfile.isEmpty()),
                    session.recommendedRetryProfile.isEmpty() ? "Retry requested" : "Safe retry requested"));
        }
        dialog.show();
    }

    private StringBuilder phaseDetailText(ValidationPhaseRecord phase) {
        StringBuilder message = new StringBuilder();
        message.append("Status: ").append(phase.status).append('\n');
        if (phase.running() && phase.progress > 0) message.append("Progress: ").append(phase.progress).append("%\n");
        if (!phase.progressDetail.isEmpty()) message.append("Progress detail: ").append(phase.progressDetail).append('\n');
        message.append("Attempts: ").append(phase.attempts).append('\n');
        if (!phase.lastVerdict.isEmpty()) message.append("Last verdict: ").append(phase.lastVerdict).append('\n');
        if (phase.signal > 0) message.append("Signal: ").append(phase.signal).append('\n');
        if (!phase.workspace.isEmpty()) message.append("Workspace: ").append(phase.workspace).append('\n');
        message.append("Executor: ").append(session.cleanExecutorKind());
        if (!session.executorName.isEmpty()) message.append('/').append(session.executorName);
        message.append(" via ").append(session.executorTransport).append('\n');
        String command = ValidationEvidenceActivity.commandText(phase.commandJson);
        if (!command.isEmpty()) message.append("Command: ").append(command).append('\n');
        if (!phase.invalidatedReason.isEmpty()) message.append("Invalidated: ").append(phase.invalidatedReason).append('\n');
        if (!phase.failureSummary.isEmpty()) message.append("\n").append(phase.failureSummary).append('\n');
        if (!phase.logPath.isEmpty()) message.append("\nLog: ").append(phase.logPath).append('\n');
        if (!phase.issuesJson.equals("[]")) message.append("Diagnostics are available in Open evidence.\n");
        if (!phase.classificationEvidenceJson.equals("[]")) message.append("Classifier evidence is available.\n");
        if (!phase.supervisorEventsJson.equals("[]")) message.append("Supervisor events are available.\n");
        if (!phase.attemptHistory.isEmpty()) {
            message.append("\nAttempt history:\n");
            for (ValidationAttemptRecord attempt : phase.attemptHistory) {
                message.append("• ").append(attempt.verdict);
                if (attempt.returnCode >= 0) message.append(" · exit ").append(attempt.returnCode);
                if (attempt.signal > 0) message.append(" · signal ").append(attempt.signal);
                if (attempt.durationMs > 0) message.append(" · ").append(formatDuration(attempt.durationMs));
                if (!attempt.category.isEmpty()) message.append(" · ").append(attempt.category);
                message.append('\n');
            }
        }
        return message;
    }

    private void renderPhaseDetailPane(ValidationPhaseRecord phase) {
        if (phaseDetailPane == null) return;
        phaseDetailPane.removeAllViews();
        phaseDetailPane.addView(ui.sectionTitle("Phase details"));
        if (phase == null) {
            phaseDetailPane.addView(ui.text("Select a phase to inspect attempts and evidence.", UiTypography.Role.BODY, ui.palette.muted));
            return;
        }
        phaseDetailPane.addView(ui.text(phase.label, UiTypography.Role.LARGE_VALUE, phaseColor(phase)));
        TextView details = ui.text(phaseDetailText(phase).toString(), UiTypography.Role.BODY_SMALL, ui.palette.text);
        details.setTextIsSelectable(true);
        phaseDetailPane.addView(details);
        phaseDetailPane.addView(ui.secondaryButton("Open log and evidence", v -> openEvidence(phase)));
        if (!ValidationEvidenceActivity.commandText(phase.commandJson).isEmpty()) {
            phaseDetailPane.addView(ui.secondaryButton("Copy command", v -> copyCommand(phase)));
        }
        if (phase.failed() || phase.status.equals("interrupted") || phase.status.equals("stale")) {
            if (!session.recommendedRetryProfile.isEmpty()) {
                phaseDetailPane.addView(ui.button("Retry safely", ui.palette.green,
                        v -> launch(ValidationController.retry(activity, session, true), "Safe retry requested")));
            }
            phaseDetailPane.addView(ui.button("Retry phase", ui.palette.green,
                    v -> launch(ValidationController.retry(activity, session, false), "Retry requested")));
        }
        phaseDetailPane.addView(ui.secondaryButton("Reset phase", v -> confirmReset(phase, false)));
    }

    private void openEvidence(ValidationPhaseRecord phase) {
        activity.startActivity(new Intent(activity, ValidationEvidenceActivity.class)
                .putExtra(ValidationEvidenceActivity.EXTRA_SESSION_ID, session.sessionId)
                .putExtra(ValidationEvidenceActivity.EXTRA_PHASE_KEY, phase.key));
    }

    private void copyCommand(ValidationPhaseRecord phase) {
        String command = ValidationEvidenceActivity.commandText(phase.commandJson);
        android.content.ClipboardManager clipboard = activity.getSystemService(android.content.ClipboardManager.class);
        if (clipboard == null || command.isEmpty()) {
            Toast.makeText(activity, "No command is available to copy", Toast.LENGTH_SHORT).show();
            return;
        }
        clipboard.setPrimaryClip(android.content.ClipData.newPlainText("Validation command", command));
        Toast.makeText(activity, "Command copied", Toast.LENGTH_SHORT).show();
    }

    private void launchRun() {
        if (ValidationScope.SELECTED.equals(scope.mode) && scope.selectedPhases.isEmpty()) {
            Toast.makeText(activity, "Choose at least one phase", Toast.LENGTH_LONG).show();
            return;
        }
        if (ValidationScope.THROUGH.equals(scope.mode) && scope.throughPhase.isEmpty()) {
            Toast.makeText(activity, "Choose a phase checkpoint", Toast.LENGTH_LONG).show();
            return;
        }
        launch(ValidationController.run(activity, session, scope), "Validation started");
    }

    private void chooseResetPhase(boolean from) {
        String[] labels = new String[session.phases.size()];
        for (int i = 0; i < labels.length; i++) labels[i] = session.phases.get(i).label;
        new AlertDialog.Builder(activity).setTitle(from ? "Restart from phase" : "Reset phase")
                .setItems(labels, (dialog, which) -> confirmReset(session.phases.get(which), from)).show();
    }

    private void confirmReset(ValidationPhaseRecord phase, boolean from) {
        new AlertDialog.Builder(activity).setTitle(from ? "Restart from " + phase.label + "?" : "Reset " + phase.label + "?")
                .setMessage("Attempt evidence stays archived. Current verdicts for the affected phase graph will be cleared.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Reset", (d, w) -> launch(
                        ValidationController.reset(activity, session, phase.key, from, false), "Reset requested")).show();
    }

    private void confirmResetAll() {
        new AlertDialog.Builder(activity).setTitle("Reset all validation progress?")
                .setMessage("Applied files and the rollback point are preserved. Attempt history remains available as evidence.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Reset progress", (d, w) -> launch(
                        ValidationController.reset(activity, session, "", false, true), "Reset requested")).show();
    }

    private void confirmStop() {
        new AlertDialog.Builder(activity).setTitle("Stop the active phase now?")
                .setMessage("Devtool will terminate the child safely and return the phase to a retryable state. It will not roll back the patch.")
                .setNegativeButton("Keep running", null)
                .setPositiveButton("Stop now", (d, w) -> launch(ValidationController.stop(activity, session), "Stop requested")).show();
    }

    private void confirmAbort(boolean rollback) {
        String title = rollback ? "Roll back applied changes?" : "Discard session and keep changes?";
        String message = rollback
                ? "Repository: " + repo()
                    + "\nRepository identity: " + session.repositoryId
                    + "\nRollback state: " + (session.rollbackStatus.isEmpty() ? "ready" : session.rollbackStatus)
                    + "\nPaths: " + session.rollbackVerifiedPaths + " verified of " + session.rollbackTotalPaths
                    + "\nBackup health: " + (session.rollbackBackupHealthy ? "verified" : "not verified")
                    + "\nUncommitted changes: " + (session.uncommittedChanges ? "present; review before rollback" : "none reported")
                    + (session.rollbackError.isEmpty() ? "" : "\nRollback warning: " + session.rollbackError)
                    + "\n\nDevtool will stage, restore, and verify every journaled path before closing the transaction."
                : "The applied tree is retained, but it will no longer carry a completed validation verdict.";
        new AlertDialog.Builder(activity).setTitle(title).setMessage(message)
                .setNegativeButton("Cancel", null)
                .setPositiveButton(rollback ? "Roll back" : "Keep changes", (d, w) -> launch(
                        ValidationController.abort(activity, session, rollback), rollback ? "Rollback requested" : "Session discard requested"))
                .show();
    }

    private void launch(TermuxCommandRunner.LaunchResult result, String success) {
        ValidationController.showLaunchResult(activity, result, success);
        if (result.started) refresh.run();
    }

    private void addAction(LinearLayout bar, String label, boolean danger, View.OnClickListener listener) {
        Button button = danger ? ui.secondaryButton(label, listener) : ui.button(label, ui.palette.green, listener);
        if (danger) button.setTextColor(ui.palette.red);
        LinearLayout.LayoutParams params = ui.verticalActions()
                ? new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                : new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        params.setMargins(ui.dp(4), 0, ui.dp(4), 0);
        button.setLayoutParams(params);
        bar.addView(button);
    }

    private Spinner spinner(String[] values, int selection) {
        Spinner spinner = new Spinner(activity);
        ArrayAdapter<String> adapter = new ArrayAdapter<>(activity, android.R.layout.simple_spinner_item, values);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        spinner.setAdapter(adapter);
        if (values.length > 0) spinner.setSelection(Math.max(0, Math.min(values.length - 1, selection)));
        spinner.setMinimumHeight(ui.dp(48));
        return spinner;
    }

    private void restoreScope() {
        if (session == null) return;
        scope.mode = ValidationScope.supported(session.scopeMode) ? session.scopeMode : ValidationScope.NEXT;
        scope.throughPhase = session.scopeThrough;
        try {
            JSONArray array = new JSONArray(session.scopeSelectedJson);
            for (int i = 0; i < array.length(); i++) scope.selectedPhases.add(array.optString(i));
        } catch (JSONException | RuntimeException ignored) {}
        if (scope.throughPhase.isEmpty() && !session.phases.isEmpty()) scope.throughPhase = session.phases.get(session.phases.size() - 1).key;
    }

    private int stateColor() {
        if (session.blocked()) return ui.palette.red;
        if (session.validated()) return session.state.equals("validated-uncommitted") ? ui.palette.yellow : ui.palette.green;
        if (session.paused()) return ui.palette.yellow;
        return ui.palette.cyan;
    }

    private String stateLabel() {
        if (session.effectiveState.equals("pausing")) return "PAUSING";
        if (session.effectiveState.equals("stopping")) return "STOPPING";
        if (session.blocked()) return "BLOCKED";
        if (session.state.equals("validated-uncommitted")) return "COMMIT PENDING";
        if (session.validated()) return "VALIDATED";
        if (session.paused()) return "PAUSED";
        return "RUNNING";
    }

    private String headerDetail() {
        ValidationPhaseRecord active = session.activePhase();
        if (active != null) return "Running " + active.label;
        ValidationPhaseRecord failed = session.failedPhase();
        if (failed != null) return failed.label + " failed. Applied changes and rollback point are retained.";
        ValidationPhaseRecord next = session.nextPhase();
        if (next != null) return "Next: " + next.label + ". Failure action: " + session.failureAction + ".";
        return session.validated() ? "All required phases passed." : "No runnable phase is currently available.";
    }

    private int phaseColor(ValidationPhaseRecord phase) {
        if (phase.failed()) return ui.palette.red;
        if (phase.passed()) return ui.palette.green;
        if (phase.running()) return ui.palette.cyan;
        if (phase.status.equals("stale") || phase.status.equals("interrupted")) return ui.palette.yellow;
        return ui.palette.muted;
    }

    private String repo() { return !session.repoRoot.isEmpty() ? session.repoRoot : session.projectRoot; }

    private static int scopeIndex(String mode) {
        if (ValidationScope.THROUGH.equals(mode)) return 1;
        if (ValidationScope.SELECTED.equals(mode)) return 2;
        if (ValidationScope.REMAINING.equals(mode)) return 3;
        return 0;
    }

    private static String humanEnvironment(String value) {
        String clean = value == null ? "" : value.toLowerCase(Locale.ROOT);
        return clean.contains("root") ? "Root bridge" : clean.contains("chroot") ? "Chroot" : "Termux";
    }

    private static String formatDuration(long durationMs) {
        long seconds = Math.max(0, durationMs / 1000L);
        return seconds < 60 ? seconds + "s" : seconds / 60 + "m " + seconds % 60 + "s";
    }

    private interface PositionConsumer { void accept(int position); }
    private static final class SimpleItemListener implements android.widget.AdapterView.OnItemSelectedListener {
        private final PositionConsumer consumer;
        SimpleItemListener(PositionConsumer consumer) { this.consumer = consumer; }
        @Override public void onItemSelected(android.widget.AdapterView<?> parent, View view, int position, long id) { consumer.accept(position); }
        @Override public void onNothingSelected(android.widget.AdapterView<?> parent) {}
    }
}
