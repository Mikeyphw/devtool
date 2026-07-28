package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.widget.Toast;

import org.json.JSONException;

final class ValidationController {
    private ValidationController() {}

    static TermuxCommandRunner.LaunchResult refresh(Context context, String repo, String sessionId, boolean preferRoot) {
        ValidationSessionRecord cached = ValidationStore.find(context, sessionId);
        if (cached != null) return executeSession(context, cached, "status", ValidationCommandBuilder.status(repo(cached), sessionId));
        return execute(context, null, repo, sessionId, "status", ValidationCommandBuilder.status(repo, sessionId), preferRoot);
    }

    static TermuxCommandRunner.LaunchResult plan(Context context, ValidationSessionRecord session) {
        return executeSession(context, session, "plan", ValidationCommandBuilder.plan(repo(session), session.sessionId));
    }

    static TermuxCommandRunner.LaunchResult run(Context context, ValidationSessionRecord session, ValidationScope scope) {
        if (session == null) return failed("No validation session is available");
        ValidationStore.updateScope(context, session.sessionId, scope);
        return executeSession(context, session, "run", ValidationCommandBuilder.run(repo(session), session, scope));
    }

    static TermuxCommandRunner.LaunchResult pause(Context context, ValidationSessionRecord session) {
        return control(context, session, "pause", ValidationCommandBuilder.pause(repo(session), session));
    }

    static TermuxCommandRunner.LaunchResult cancelPause(Context context, ValidationSessionRecord session) {
        return control(context, session, "cancel-pause", ValidationCommandBuilder.cancelPause(repo(session), session));
    }

    static TermuxCommandRunner.LaunchResult stop(Context context, ValidationSessionRecord session) {
        return control(context, session, "stop", ValidationCommandBuilder.stop(repo(session), session));
    }

    static TermuxCommandRunner.LaunchResult retry(Context context, ValidationSessionRecord session, boolean safe) {
        if (session == null) return failed("No validation session is available");
        String command = ValidationCommandBuilder.retry(repo(session), session);
        if (safe && !session.recommendedRetryProfile.isEmpty()) command += " --resource-profile '" + session.recommendedRetryProfile.replace("'", "'\\''") + "'";
        return control(context, session, safe ? "retry-safe" : "retry", command);
    }

    static TermuxCommandRunner.LaunchResult resume(Context context, ValidationSessionRecord session) {
        return control(context, session, "resume", ValidationCommandBuilder.resume(repo(session), session));
    }

    static TermuxCommandRunner.LaunchResult reset(Context context, ValidationSessionRecord session, String phase, boolean from, boolean all) {
        return control(context, session, "reset", ValidationCommandBuilder.reset(repo(session), session, phase, from, all));
    }

    static TermuxCommandRunner.LaunchResult finalizeSession(Context context, ValidationSessionRecord session) {
        return control(context, session, "finalize", ValidationCommandBuilder.finalizeSession(repo(session), session));
    }

    static TermuxCommandRunner.LaunchResult recover(Context context, ValidationSessionRecord session) {
        return control(context, session, "recover", ValidationCommandBuilder.recover(repo(session), session));
    }

    static TermuxCommandRunner.LaunchResult abort(Context context, ValidationSessionRecord session, boolean rollback) {
        return control(context, session, rollback ? "abort-rollback" : "abort-keep",
                ValidationCommandBuilder.abort(repo(session), session, rollback));
    }

    private static TermuxCommandRunner.LaunchResult control(Context context, ValidationSessionRecord session,
                                                             String action, String command) {
        if (session == null) return failed("No validation session is available");
        return executeSession(context, session, action, command);
    }

    private static TermuxCommandRunner.LaunchResult executeSession(Context context, ValidationSessionRecord session,
                                                                    String action, String command) {
        if (session == null) return failed("No validation session is available");
        if (!action.equals("status") && !action.equals("plan")) {
            String identityError = validateIdentity(context, session);
            if (!identityError.isEmpty()) return failed(identityError);
        }
        return execute(context, session, repo(session), session.sessionId, action, command, prefersRoot(session));
    }

    private static TermuxCommandRunner.LaunchResult execute(Context context, ValidationSessionRecord session,
                                                             String repo, String sessionId, String action,
                                                             String command, boolean preferRoot) {
        markPending(context, sessionId, action);
        String operation = "validation-" + action + "|" + (sessionId == null ? "" : sessionId);
        if (session != null && session.cleanExecutorKind().equals("chroot")) {
            if (session.executorName.isEmpty()) return failed("Validation session is missing its chroot identity");
            String wrapped = "sudo chrootctl --name " + shell(session.executorName)
                    + " exec -- zsh -ilc " + shell(command);
            return TermuxCommandRunner.runUtilityCommand(context, wrapped, "~/", true, operation);
        }
        if (preferRoot || (session != null && session.cleanExecutorKind().equals("root"))) {
            if (!RootBridgeManager.isReady(context)) return failed("The validation session requires the connected root bridge");
            return RootBridgeManager.runValidationControl(context, command, repo, operation);
        }
        return TermuxCommandRunner.runUtilityCommand(context, command, repo, true, operation);
    }

    static void handleResult(Context context, String operation, int exitCode,
                             String stdout, String stderr, String error, String environment) {
        String sessionHint = "";
        int delimiter = operation == null ? -1 : operation.indexOf('|');
        if (delimiter >= 0 && delimiter + 1 < operation.length()) sessionHint = operation.substring(delimiter + 1);
        String failure = error == null ? "" : error.trim();
        if (failure.isEmpty() && stdout != null && !stdout.trim().isEmpty()) {
            try {
                ValidationBridgeParser.Parsed parsed = ValidationBridgeParser.parse(stdout);
                ValidationSessionRecord incoming = parsed.session;
                String resolvedSessionId = incoming.sessionId.isEmpty() ? sessionHint : incoming.sessionId;
                ValidationSessionRecord existing = ValidationStore.find(context, resolvedSessionId);
                if (existing != null && incoming.revision < existing.revision) {
                    new ValidationNotificationController(context).post(existing);
                    return;
                }
                mergeLocal(incoming, existing);
                if (environment != null && !environment.trim().isEmpty()) incoming.executorTransport = environment.trim();
                incoming.lastMessage = parsed.message;
                incoming.lastReason = parsed.reason;
                if (!parsed.accepted && !parsed.message.isEmpty()) incoming.lastError = parsed.message;
                if (existing != null && !existing.repositoryId.isEmpty() && !incoming.repositoryId.isEmpty()
                        && !existing.repositoryId.equals(incoming.repositoryId)) {
                    ValidationStore.reportError(context, existing.sessionId,
                            "Rejected validation response for a different repository identity");
                    return;
                }
                if (!ValidationStore.save(context, incoming)) {
                    ValidationSessionRecord current = ValidationStore.find(context, resolvedSessionId);
                    if (current != null) new ValidationNotificationController(context).post(current);
                    return;
                }
                new ValidationNotificationController(context).post(incoming);
                String action = operationAction(operation);
                if (parsed.accepted && !action.equals("status") && !action.equals("plan")) {
                    refresh(context, repo(incoming), incoming.sessionId, prefersRoot(incoming));
                }
                return;
            } catch (JSONException parseError) {
                failure = "Could not parse validation response: " + parseError.getMessage();
            }
        }
        if (failure.isEmpty()) failure = first(stderr, stdout, "Validation command exited " + exitCode);
        ValidationStore.reportError(context, sessionHint, failure);
        ValidationSessionRecord existing = ValidationStore.find(context, sessionHint);
        if (existing != null) {
            existing.lastError = failure;
            existing.lastMessage = failure;
            existing.updatedAtMs = System.currentTimeMillis();
            ValidationStore.save(context, existing);
            new ValidationNotificationController(context).post(existing);
        }
    }

    static void showLaunchResult(Context context, TermuxCommandRunner.LaunchResult result, String success) {
        Toast.makeText(context, result.started ? success : "Validation action failed: " + result.error,
                result.started ? Toast.LENGTH_SHORT : Toast.LENGTH_LONG).show();
    }

    private static void mergeLocal(ValidationSessionRecord incoming, ValidationSessionRecord existing) {
        if (existing == null) return;
        boolean sparse = incoming.phases.isEmpty() && incoming.repoRoot.isEmpty() && incoming.projectRoot.isEmpty();
        if (incoming.repositoryId.isEmpty()) incoming.repositoryId = existing.repositoryId;
        if (incoming.repoRoot.isEmpty()) incoming.repoRoot = existing.repoRoot;
        if (incoming.projectRoot.isEmpty()) incoming.projectRoot = existing.projectRoot;
        if (incoming.executionEnvironment.isEmpty()) incoming.executionEnvironment = existing.executionEnvironment;
        if (incoming.executorKind.isEmpty()) incoming.executorKind = existing.executorKind;
        if (incoming.executorName.isEmpty()) incoming.executorName = existing.executorName;
        if (incoming.executorTransport.isEmpty()) incoming.executorTransport = existing.executorTransport;
        if (incoming.executorWorkdir.isEmpty()) incoming.executorWorkdir = existing.executorWorkdir;
        if (incoming.executorLinuxUser.isEmpty()) incoming.executorLinuxUser = existing.executorLinuxUser;
        incoming.scopeMode = existing.scopeMode;
        incoming.scopeThrough = existing.scopeThrough;
        incoming.scopeSelectedJson = existing.scopeSelectedJson;
        if (incoming.createdAtMs <= 0) incoming.createdAtMs = existing.createdAtMs;
        if (!sparse) return;
        incoming.projectType = existing.projectType;
        incoming.target = existing.target;
        incoming.variant = existing.variant;
        incoming.artifact = existing.artifact;
        incoming.validationMode = existing.validationMode;
        incoming.failureAction = existing.failureAction;
        incoming.resourceProfile = existing.resourceProfile;
        incoming.requiredCheckpointName = existing.requiredCheckpointName;
        incoming.requiredCheckpointSatisfied = existing.requiredCheckpointSatisfied;
        incoming.recoveryAvailable = existing.recoveryAvailable;
        incoming.recommendedRetryProfile = existing.recommendedRetryProfile;
        incoming.transactionPath = existing.transactionPath;
        incoming.pausedReason = existing.pausedReason;
        incoming.invalidatedFrom = existing.invalidatedFrom;
        incoming.completedPhases = existing.completedPhases;
        incoming.phaseTotal = existing.phaseTotal;
        incoming.latestEventId = existing.latestEventId;
        incoming.rollbackStatus = existing.rollbackStatus;
        incoming.rollbackError = existing.rollbackError;
        incoming.rollbackTotalPaths = existing.rollbackTotalPaths;
        incoming.rollbackVerifiedPaths = existing.rollbackVerifiedPaths;
        incoming.rollbackBackupHealthy = existing.rollbackBackupHealthy;
        incoming.uncommittedChanges = existing.uncommittedChanges;
        incoming.reportPath = existing.reportPath;
        incoming.reportGeneratedAt = existing.reportGeneratedAt;
        incoming.finalVerdict = existing.finalVerdict;
        incoming.commitStatus = existing.commitStatus;
        incoming.commitHash = existing.commitHash;
        incoming.initialGitHead = existing.initialGitHead;
        incoming.finalGitHead = existing.finalGitHead;
        incoming.reportWarnings = existing.reportWarnings;
        incoming.reportErrors = existing.reportErrors;
        incoming.phases.addAll(existing.phases);
    }

    private static void markPending(Context context, String sessionId, String action) {
        ValidationSessionRecord session = ValidationStore.find(context, sessionId);
        if (session == null || action.equals("status") || action.equals("plan")) return;
        if (action.equals("pause")) session.effectiveState = "pausing";
        else if (action.equals("stop")) session.effectiveState = "stopping";
        else if (action.equals("cancel-pause") || action.equals("resume") || action.startsWith("retry") || action.equals("run")) {
            session.effectiveState = "running";
        }
        session.lastMessage = "Requesting " + action.replace('-', ' ');
        session.updatedAtMs = System.currentTimeMillis();
        ValidationStore.save(context, session);
        new ValidationNotificationController(context).post(session);
    }

    private static String validateIdentity(Context context, ValidationSessionRecord session) {
        if (session.repositoryId.isEmpty()) return "Validation session repository identity is unavailable; refresh it before controlling the session";
        if (!session.identityMatches(session.repositoryId)) return "Validation session repository identity no longer matches its stored repository";
        String kind = session.cleanExecutorKind();
        if (kind.equals("chroot") && session.executorName.isEmpty()) return "Validation session chroot name is unavailable";
        if (kind.equals("root") && !RootBridgeManager.isReady(context)) return "Validation session requires the connected root bridge";
        return "";
    }


    private static String operationAction(String operation) {
        String value = operation == null ? "" : operation;
        int start = value.startsWith("validation-") ? "validation-".length() : 0;
        int end = value.indexOf('|', start);
        return value.substring(start, end < 0 ? value.length() : end);
    }

    private static boolean prefersRoot(ValidationSessionRecord session) {
        return session != null && (session.cleanExecutorKind().equals("root")
                || session.executorTransport.toLowerCase(java.util.Locale.ROOT).contains("root"));
    }

    private static String repo(ValidationSessionRecord session) {
        if (session == null) return "~/";
        return !session.repoRoot.isEmpty() ? session.repoRoot : !session.projectRoot.isEmpty() ? session.projectRoot : "~/";
    }

    private static String shell(String value) {
        String clean = value == null ? "" : value;
        return "'" + clean.replace("'", "'\\''") + "'";
    }

    private static TermuxCommandRunner.LaunchResult failed(String error) {
        return new TermuxCommandRunner.LaunchResult(false, -1, error);
    }

    private static String first(String... values) {
        for (String value : values) if (value != null && !value.trim().isEmpty()) return value.trim();
        return "Validation control failed";
    }
}
