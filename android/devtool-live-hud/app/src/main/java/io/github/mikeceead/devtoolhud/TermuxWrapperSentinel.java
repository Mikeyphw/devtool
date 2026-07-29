package io.github.mikeceead.devtoolhud;

/**
 * Shell-side sentinel checkpoints for Termux RUN_COMMAND launches.
 *
 * These constants are intentionally separate from the Java-side launch trace so
 * tests and support bundles can distinguish Android intent dispatch from the
 * wrapper that actually started inside Termux.
 */
final class TermuxWrapperSentinel {
    static final String TERMUX_SHELL_STARTED = RunCommandTrace.TERMUX_SHELL_STARTED;
    static final String WRAPPER_STARTED = RunCommandTrace.TERMUX_WRAPPER_STARTED;
    static final String SHELL_RESOLVED = RunCommandTrace.SHELL_RESOLVED;
    static final String TOKEN_MARKERS_OK = RunCommandTrace.TOKEN_MARKERS_OK;
    static final String TOKEN_MARKERS_MISSING = RunCommandTrace.TOKEN_MARKERS_MISSING;
    static final String WORKDIR_CHECKED = RunCommandTrace.WORKDIR_CHECKED;
    static final String WORKDIR_MISSING = RunCommandTrace.WORKDIR_MISSING;
    static final String ENV_READY = RunCommandTrace.ENV_READY;
    static final String COMMAND_FOUND = RunCommandTrace.COMMAND_FOUND;
    static final String COMMAND_LOOKUP_SKIPPED = RunCommandTrace.COMMAND_LOOKUP_SKIPPED;
    static final String COMMAND_NOT_FOUND = RunCommandTrace.COMMAND_NOT_FOUND;
    static final String EXEC_STARTING = RunCommandTrace.EXEC_STARTING;
    static final String EXIT_CODE_RECORDED = RunCommandTrace.EXIT_CODE_RECORDED;
    static final String FIRST_OUTPUT = RunCommandTrace.FIRST_OUTPUT;

    private TermuxWrapperSentinel() {}

    static String wrapperPreamble(String bridgeComponent, String bridgeAction,
                                  String runId, String token, String primaryCommand) {
        StringBuilder shell = new StringBuilder();
        shell.append("__devtool_wrapper_counter=0; ");
        shell.append("__devtool_wrapper_component=").append(shellQuote(bridgeComponent)).append("; ");
        shell.append("__devtool_wrapper_action=").append(shellQuote(bridgeAction)).append("; ");
        shell.append("__devtool_wrapper_run_id=").append(shellQuote(runId)).append("; ");
        shell.append("__devtool_wrapper_token=").append(shellQuote(token)).append("; ");
        shell.append("__devtool_wrapper_command=").append(shellQuote(primaryCommand)).append("; ");
        shell.append("__devtool_wrapper_now_ms() { __devtool_wrapper_counter=$((__devtool_wrapper_counter + 1)); echo $(($(date +%s) * 1000 + __devtool_wrapper_counter)); }; ");
        shell.append("__devtool_wrapper_emit() { ");
        shell.append("__devtool_wrapper_checkpoint=\"$1\"; shift; ");
        shell.append("__devtool_wrapper_message=\"$*\"; ");
        shell.append("[ -z \"$__devtool_wrapper_message\" ] && __devtool_wrapper_message=\"$__devtool_wrapper_checkpoint\"; ");
        shell.append("/system/bin/am broadcast --user 0 -f 0x20 -n \"$__devtool_wrapper_component\" ");
        shell.append("-a \"$__devtool_wrapper_action\" ");
        shell.append("--es token \"$__devtool_wrapper_token\" ");
        shell.append("--es run_id \"$__devtool_wrapper_run_id\" ");
        shell.append("--es status waiting --es task 'Starting Devtool' --es phase 'Termux wrapper' ");
        shell.append("--es detail \"$__devtool_wrapper_message\" ");
        shell.append("--es trace_checkpoint \"$__devtool_wrapper_checkpoint\" ");
        shell.append("--es trace_message \"$__devtool_wrapper_message\" ");
        shell.append("--es wrapper_stage \"$__devtool_wrapper_checkpoint\" ");
        shell.append("--es wrapper_command \"$__devtool_wrapper_command\" ");
        shell.append("--ei progress 0 --ei phase_progress 0 --ez indeterminate true ");
        shell.append("--el updated_at_ms $(__devtool_wrapper_now_ms) >/dev/null 2>&1 || true; ");
        shell.append("}; ");
        return shell.toString();
    }

    static String wrapperChecks(String selectedShell) {
        StringBuilder shell = new StringBuilder();
        shell.append("__devtool_wrapper_emit ").append(shellQuote(TERMUX_SHELL_STARTED)).append(" 'Termux shell started before devtool progress'; ");
        shell.append("__devtool_wrapper_emit ").append(shellQuote(WRAPPER_STARTED)).append(" 'Termux wrapper started'; ");
        shell.append("__devtool_wrapper_emit ").append(shellQuote(SHELL_RESOLVED)).append(" ").append(shellQuote("Shell resolved: " + selectedShell)).append("; ");
        shell.append("if [ -n \"$DEVTOOL_ANDROID_HUD_TOKEN\" ] && [ -n \"$DEVTOOL_ANDROID_HUD_PACKAGE\" ] && [ -n \"$DEVTOOL_ANDROID_HUD_COMPONENT\" ]; then ");
        shell.append("__devtool_wrapper_emit ").append(shellQuote(TOKEN_MARKERS_OK)).append(" 'Bridge token and component markers exported'; ");
        shell.append("else __devtool_wrapper_emit ").append(shellQuote(TOKEN_MARKERS_MISSING)).append(" 'Bridge token or component markers are missing'; fi; ");
        shell.append("if [ -d \"$PWD\" ]; then __devtool_wrapper_emit ").append(shellQuote(WORKDIR_CHECKED)).append(" \"Workdir exists: $PWD\"; ");
        shell.append("else __devtool_wrapper_emit ").append(shellQuote(WORKDIR_MISSING)).append(" \"Workdir missing or unreadable: $PWD\"; fi; ");
        shell.append("__devtool_wrapper_emit ").append(shellQuote(ENV_READY)).append(" 'Wrapper environment captured'; ");
        shell.append("if [ -z \"$__devtool_wrapper_command\" ]; then __devtool_wrapper_emit ").append(shellQuote(COMMAND_LOOKUP_SKIPPED)).append(" 'Command lookup skipped for composite shell command'; ");
        shell.append("elif command -v \"$__devtool_wrapper_command\" >/dev/null 2>&1 || [ -x \"$__devtool_wrapper_command\" ]; then __devtool_wrapper_emit ").append(shellQuote(COMMAND_FOUND)).append(" \"Command found: $__devtool_wrapper_command\"; ");
        shell.append("else __devtool_wrapper_emit ").append(shellQuote(COMMAND_NOT_FOUND)).append(" \"Command not found before exec: $__devtool_wrapper_command\"; fi; ");
        shell.append("__devtool_wrapper_emit ").append(shellQuote(EXEC_STARTING)).append(" 'Starting requested command'; ");
        return shell.toString();
    }

    static String exitReporter() {
        return "; __devtool_wrapper_exit=$?; __devtool_wrapper_emit "
                + shellQuote(EXIT_CODE_RECORDED)
                + " \"Command exited with code $__devtool_wrapper_exit\"; exit $__devtool_wrapper_exit";
    }

    private static String shellQuote(String value) {
        String clean = value == null ? "" : value;
        return "'" + clean.replace("'", "'\\''") + "'";
    }
}
