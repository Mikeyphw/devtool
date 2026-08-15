package io.github.mikeceead.devtoolhud;

import java.util.ArrayList;
import java.util.List;

final class ValidationSessionRecord {
    String sessionId = "";
    long revision;
    String state = "ready";
    String effectiveState = "ready";
    String projectRoot = "";
    String repoRoot = "";
    String repositoryId = "";
    String projectType = "auto";
    String target = "";
    String variant = "";
    String artifact = "";
    String validationMode = "standalone";
    String executionEnvironment = "native-termux";
    String executorKind = "native-termux";
    String executorName = "";
    String executorTransport = "termux";
    String executorWorkdir = "";
    String executorLinuxUser = "";
    String failureAction = "pause";
    String resourceProfile = "automatic";
    String requiredCheckpointName = "finalize";
    String recommendedRetryProfile = "";
    String transactionPath = "";
    String pausedReason = "";
    String invalidatedFrom = "";
    String lastError = "";
    String lastMessage = "";
    String lastReason = "";
    String latestEventId = "";
    String scopeMode = ValidationScope.NEXT;
    String scopeThrough = "";
    String scopeSelectedJson = "[]";
    String rollbackStatus = "";
    String rollbackError = "";
    String reportPath = "";
    String reportGeneratedAt = "";
    String finalVerdict = "";
    String commitStatus = "not-requested";
    String commitHash = "";
    String initialGitHead = "";
    String finalGitHead = "";
    long createdAtMs;
    long updatedAtMs;
    boolean recoveryAvailable;
    boolean requiredCheckpointSatisfied;
    boolean rollbackBackupHealthy;
    boolean uncommittedChanges;
    int completedPhases;
    int phaseTotal;
    int rollbackTotalPaths;
    int rollbackVerifiedPaths;
    int reportWarnings;
    int reportErrors;
    final List<ValidationPhaseRecord> phases = new ArrayList<>();

    ValidationPhaseRecord activePhase() {
        for (ValidationPhaseRecord phase : phases) if (phase.running()) return phase;
        return null;
    }

    ValidationPhaseRecord failedPhase() {
        for (ValidationPhaseRecord phase : phases) if (phase.failed()) return phase;
        return null;
    }

    ValidationPhaseRecord nextPhase() {
        for (ValidationPhaseRecord phase : phases) if (phase.runnable()) return phase;
        return null;
    }

    int overallProgress() {
        return phaseTotal <= 0 ? 0 : Math.max(0, Math.min(100, Math.round(completedPhases * 100f / phaseTotal)));
    }

    boolean running() { return effectiveState.equals("running") || effectiveState.equals("pausing") || effectiveState.equals("stopping"); }
    boolean blocked() { return state.equals("blocked"); }
    boolean paused() { return state.equals("paused") || state.equals("ready") || state.equals("created"); }
    boolean validated() { return state.equals("validated") || state.equals("completed") || state.equals("validated-uncommitted"); }
    boolean terminal() { return state.equals("completed") || state.startsWith("aborted-"); }
    boolean hasReport() { return !reportPath.isEmpty() || !finalVerdict.isEmpty() || terminal() || blocked() || validated(); }
    boolean recoveryRequired() { return state.equals("recovery-required") || state.equals("rollback-failed") || state.equals("rollback-running"); }

    RepositoryLocation repositoryLocation() {
        String kind = cleanExecutorKind();
        String path = !repoRoot.isEmpty() ? repoRoot : projectRoot;
        if (kind.equals("chroot")) return RepositoryLocation.chroot(executorName, executorLinuxUser, path);
        if (kind.equals("root")) return RepositoryLocation.root(path);
        return RepositoryLocation.termux(path);
    }

    String computedRepositoryId() { return repositoryLocation().id(); }

    String cleanExecutorKind() {
        String kind = executorKind == null ? "" : executorKind.trim().toLowerCase(java.util.Locale.ROOT);
        if (kind.isEmpty()) kind = executionEnvironment == null ? "" : executionEnvironment.trim().toLowerCase(java.util.Locale.ROOT);
        if (kind.contains("chroot")) return "chroot";
        if (kind.contains("root")) return "root";
        return "native-termux";
    }

    boolean identityMatches(String expectedRepositoryId) {
        if (expectedRepositoryId == null || expectedRepositoryId.trim().isEmpty()) return true;
        String expected = expectedRepositoryId.trim();
        return expected.equals(repositoryId) || expected.equals(computedRepositoryId());
    }

    String title() {
        if (!target.isEmpty()) return target;
        if (!projectRoot.isEmpty()) {
            int slash = projectRoot.lastIndexOf('/');
            return slash >= 0 ? projectRoot.substring(slash + 1) : projectRoot;
        }
        return "Validation";
    }
}
