package io.github.mikeceead.devtoolhud;

final class ValidationCommandBuilder {
    private ValidationCommandBuilder() {}

    static String status(String repo, String sessionId) {
        StringBuilder command = base(repo).append(" validate status --json --events --event-limit 100");
        appendSession(command, sessionId);
        return command.toString();
    }

    static String plan(String repo, String sessionId) {
        StringBuilder command = base(repo).append(" validate plan --json");
        appendSession(command, sessionId);
        return command.toString();
    }

    static String run(String repo, ValidationSessionRecord session, ValidationScope scope) {
        StringBuilder command = base(repo).append(" validate run --json");
        appendConcurrency(command, session);
        String mode = scope == null || !ValidationScope.supported(scope.mode) ? ValidationScope.NEXT : scope.mode;
        command.append(" --scope ").append(shell(mode));
        if (ValidationScope.THROUGH.equals(mode)) command.append(" --through ").append(shell(scope.throughPhase));
        if (ValidationScope.SELECTED.equals(mode)) {
            for (String phase : scope.selectedPhases) command.append(" --phase ").append(shell(phase));
        }
        return command.toString();
    }

    static String pause(String repo, ValidationSessionRecord session) {
        return control(repo, session, "pause") + " --after-phase";
    }

    static String cancelPause(String repo, ValidationSessionRecord session) {
        return control(repo, session, "pause") + " --cancel";
    }

    static String stop(String repo, ValidationSessionRecord session) { return control(repo, session, "stop"); }
    static String retry(String repo, ValidationSessionRecord session) { return control(repo, session, "retry"); }
    static String resume(String repo, ValidationSessionRecord session) { return control(repo, session, "resume"); }
    static String finalizeSession(String repo, ValidationSessionRecord session) { return control(repo, session, "finalize"); }
    static String recover(String repo, ValidationSessionRecord session) { return control(repo, session, "recover"); }

    static String reset(String repo, ValidationSessionRecord session, String phase, boolean from, boolean all) {
        StringBuilder command = new StringBuilder(control(repo, session, "reset"));
        if (all) command.append(" --all");
        else if (from) command.append(" --from-phase ").append(shell(phase));
        else command.append(" --phase ").append(shell(phase));
        return command.toString();
    }

    static String abort(String repo, ValidationSessionRecord session, boolean rollback) {
        return control(repo, session, "abort") + (rollback ? " --rollback" : " --keep-changes");
    }

    private static String control(String repo, ValidationSessionRecord session, String action) {
        StringBuilder command = base(repo).append(" validate ").append(action).append(" --json");
        appendConcurrency(command, session);
        return command.toString();
    }

    private static StringBuilder base(String repo) {
        String clean = repo == null || repo.trim().isEmpty() ? "~/" : repo.trim();
        return new StringBuilder("devtool -r ").append(shell(clean));
    }

    private static void appendConcurrency(StringBuilder command, ValidationSessionRecord session) {
        if (session == null) return;
        appendSession(command, session.sessionId);
        if (session.revision >= 0) command.append(" --if-revision ").append(session.revision);
    }

    private static void appendSession(StringBuilder command, String sessionId) {
        if (sessionId != null && !sessionId.trim().isEmpty()) command.append(" --session ").append(shell(sessionId.trim()));
    }

    private static String shell(String value) {
        String clean = value == null ? "" : value;
        return "'" + clean.replace("'", "'\\''") + "'";
    }
}
