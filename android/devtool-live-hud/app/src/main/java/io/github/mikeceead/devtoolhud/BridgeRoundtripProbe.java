package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

import java.util.List;

/**
 * One-tap end-to-end probe for the Termux RunCommand bridge.
 *
 * The probe intentionally uses the normal TermuxCommandRunner path so it covers
 * the same permission, token, wrapper, BridgeReceiver, RunStore/debug-event and
 * Termux pending-result surfaces as a real app-launched command.
 */
final class BridgeRoundtripProbe {
    static final String ACTION_UPDATED = "io.github.mikeceead.devtoolhud.BRIDGE_ROUNDTRIP_UPDATED";
    static final String OPERATION = "roundtrip-probe";
    static final String STDOUT_MARKER = "DEVTOOL_BRIDGE_ROUNDTRIP_OK";

    private static final String PREFS = "bridge_roundtrip_probe";
    private static final String KEY_BUSY = "busy";
    private static final String KEY_STATUS = "status";
    private static final String KEY_DETAIL = "detail";
    private static final String KEY_LAST_RUN_ID = "last_run_id";
    private static final String KEY_LAST_EXECUTION_ID = "last_execution_id";
    private static final String KEY_LAST_STARTED_AT = "last_started_at";
    private static final String KEY_LAST_FINISHED_AT = "last_finished_at";
    private static final String KEY_LAST_EXIT_CODE = "last_exit_code";
    private static final String KEY_LAST_CHECKPOINTS = "last_checkpoints";

    private BridgeRoundtripProbe() {}

    static String state(Context context) {
        if (!TermuxCommandRunner.isInstalled(context)) return "termux-missing";
        if (!TermuxCommandRunner.hasPermission(context)) return "permission-needed";
        if (prefs(context).getBoolean(KEY_BUSY, false)) return "running";
        String status = prefs(context).getString(KEY_STATUS, "never-run");
        return status == null || status.trim().isEmpty() ? "never-run" : status;
    }

    static String label(Context context) {
        switch (state(context)) {
            case "passed": return "Passed";
            case "failed": return "Failed";
            case "running": return "Running";
            case "permission-needed": return "Needs access";
            case "termux-missing": return "Unavailable";
            default: return "Not run";
        }
    }

    static String detail(Context context) {
        String stored = prefs(context).getString(KEY_DETAIL, "");
        if (stored != null && !stored.trim().isEmpty()) return stored.trim();
        switch (state(context)) {
            case "passed": return "Termux launched, wrapper sent checkpoints, callback reached Android, and the result returned.";
            case "failed": return "The last bridge roundtrip failed before every checkpoint completed.";
            case "running": return "Launching a tiny Termux command and waiting for authenticated callbacks.";
            case "permission-needed": return "Grant Termux RunCommand access before testing the bridge.";
            case "termux-missing": return "Termux was not detected on this device.";
            default: return "Runs a safe one-shot Termux → Android callback test.";
        }
    }

    static String actionLabel(Context context) {
        switch (state(context)) {
            case "running": return "Running…";
            case "permission-needed": return "Grant access";
            case "termux-missing": return "Unavailable";
            default: return "Run probe";
        }
    }

    static boolean actionEnabled(Context context) {
        String state = state(context);
        return !state.equals("running") && !state.equals("termux-missing");
    }

    static TermuxCommandRunner.LaunchResult start(Context context) {
        if (!TermuxCommandRunner.isInstalled(context)) {
            return new TermuxCommandRunner.LaunchResult(false, -1, "Termux is not installed");
        }
        if (!TermuxCommandRunner.hasPermission(context)) {
            return new TermuxCommandRunner.LaunchResult(false, -1, "Run commands in Termux permission is not granted");
        }
        if (prefs(context).getBoolean(KEY_BUSY, false)) {
            return new TermuxCommandRunner.LaunchResult(false, -1, "Bridge roundtrip probe is already running");
        }
        SharedPreferences.Editor edit = prefs(context).edit()
                .putBoolean(KEY_BUSY, true)
                .putString(KEY_STATUS, "running")
                .putString(KEY_DETAIL, "Launching Termux bridge roundtrip probe")
                .putLong(KEY_LAST_STARTED_AT, System.currentTimeMillis())
                .putLong(KEY_LAST_FINISHED_AT, 0L)
                .putInt(KEY_LAST_EXIT_CODE, -1)
                .putString(KEY_LAST_CHECKPOINTS, "");
        edit.apply();
        notifyChanged(context);

        DebugRecorder.info(context, "bridge", "BRIDGE_ROUNDTRIP_REQUESTED", "BridgeRoundtripProbe",
                "", "", "", "", "Bridge roundtrip probe requested",
                DebugRecorder.details("termux_installed", true, "permission", true));
        TermuxCommandRunner.LaunchResult result = TermuxCommandRunner.runBridgeOperation(
                context, roundtripCommand(), OPERATION, STDOUT_MARKER);
        if (result.started) {
            String runId = "termux-" + result.executionId;
            prefs(context).edit()
                    .putString(KEY_LAST_RUN_ID, runId)
                    .putInt(KEY_LAST_EXECUTION_ID, result.executionId)
                    .putString(KEY_DETAIL, "Probe launched as " + runId + "; waiting for wrapper callbacks")
                    .apply();
            DebugRecorder.info(context, "bridge", "BRIDGE_ROUNDTRIP_LAUNCHED", "BridgeRoundtripProbe",
                    runId, "", "", "", "Bridge roundtrip probe launched",
                    DebugRecorder.details("execution_id", result.executionId));
        } else {
            prefs(context).edit()
                    .putBoolean(KEY_BUSY, false)
                    .putString(KEY_STATUS, "failed")
                    .putString(KEY_DETAIL, result.error)
                    .putLong(KEY_LAST_FINISHED_AT, System.currentTimeMillis())
                    .apply();
            DebugRecorder.error(context, "bridge", "BRIDGE_ROUNDTRIP_LAUNCH_FAILED", "BridgeRoundtripProbe",
                    "", "", "", "", result.error, "{}");
        }
        notifyChanged(context);
        return result;
    }

    static void handleResult(Context context, String runId, int executionId,
                             int exitCode, String stdout, String stderr, String error) {
        List<DebugEventRecord> events = DevtoolDatabase.get(context).debugEventsForRun(runId, 160);
        boolean wrapperStarted = has(events, RunCommandTrace.TERMUX_WRAPPER_STARTED);
        boolean tokenMarkersOk = has(events, RunCommandTrace.TOKEN_MARKERS_OK);
        boolean workdirChecked = has(events, RunCommandTrace.WORKDIR_CHECKED);
        boolean envReady = has(events, RunCommandTrace.ENV_READY);
        boolean execStarting = has(events, RunCommandTrace.EXEC_STARTING);
        boolean callbackReceived = has(events, RunCommandTrace.BRIDGE_ROUNDTRIP_CALLBACK_RECEIVED);
        boolean resultReceived = exitCode >= 0 || (error != null && !error.trim().isEmpty());
        boolean stdoutMarker = stdout != null && stdout.contains(STDOUT_MARKER);
        boolean cleanExit = exitCode == 0 && (error == null || error.trim().isEmpty());
        boolean ok = cleanExit && stdoutMarker && wrapperStarted && tokenMarkersOk
                && workdirChecked && envReady && execStarting && callbackReceived;
        String checkpoints = checkpoints(wrapperStarted, tokenMarkersOk, workdirChecked,
                envReady, execStarting, callbackReceived, resultReceived, stdoutMarker);
        String detail = ok ? "Bridge roundtrip passed: Termux launched, wrapper checkpoints arrived, callback was accepted, and result returned."
                : failureDetail(wrapperStarted, tokenMarkersOk, workdirChecked, envReady,
                execStarting, callbackReceived, resultReceived, stdoutMarker, exitCode, stderr, error);
        prefs(context).edit()
                .putBoolean(KEY_BUSY, false)
                .putString(KEY_STATUS, ok ? "passed" : "failed")
                .putString(KEY_DETAIL, detail)
                .putString(KEY_LAST_RUN_ID, runId == null ? "" : runId)
                .putInt(KEY_LAST_EXECUTION_ID, executionId)
                .putLong(KEY_LAST_FINISHED_AT, System.currentTimeMillis())
                .putInt(KEY_LAST_EXIT_CODE, exitCode)
                .putString(KEY_LAST_CHECKPOINTS, checkpoints)
                .apply();
        DebugRecorder.record(context, ok ? "success" : "error", "bridge",
                ok ? "BRIDGE_ROUNDTRIP_SUCCEEDED" : "BRIDGE_ROUNDTRIP_FAILED",
                "BridgeRoundtripProbe", runId, "", "", "", detail,
                DebugRecorder.details("execution_id", executionId, "exit_code", exitCode,
                        "wrapper_started", wrapperStarted, "token_markers_ok", tokenMarkersOk,
                        "workdir_checked", workdirChecked, "env_ready", envReady,
                        "exec_starting", execStarting, "callback_received", callbackReceived,
                        "result_received", resultReceived, "stdout_marker", stdoutMarker,
                        "stderr_chars", stderr == null ? 0 : stderr.length(), "error", error));
        notifyChanged(context);
    }

    static String supportSummary(Context context) {
        SharedPreferences prefs = prefs(context);
        return DebugRecorder.details(
                "state", state(context),
                "label", label(context),
                "detail", detail(context),
                "last_run_id", prefs.getString(KEY_LAST_RUN_ID, ""),
                "last_execution_id", prefs.getInt(KEY_LAST_EXECUTION_ID, -1),
                "last_started_at", prefs.getLong(KEY_LAST_STARTED_AT, 0L),
                "last_finished_at", prefs.getLong(KEY_LAST_FINISHED_AT, 0L),
                "last_exit_code", prefs.getInt(KEY_LAST_EXIT_CODE, -1),
                "last_checkpoints", prefs.getString(KEY_LAST_CHECKPOINTS, ""));
    }

    private static String roundtripCommand() {
        String checkpoint = shellQuote(RunCommandTrace.BRIDGE_ROUNDTRIP_CALLBACK_RECEIVED);
        String marker = shellQuote(STDOUT_MARKER);
        StringBuilder shell = new StringBuilder();
        shell.append("set -u; ");
        shell.append("__devtool_roundtrip_dir=\"${HOME:-}/.config/devtool\"; ");
        shell.append("__devtool_roundtrip_token_file=\"$__devtool_roundtrip_dir/android-live-hud.token\"; ");
        shell.append("__devtool_roundtrip_package_file=\"$__devtool_roundtrip_dir/android-live-hud.package\"; ");
        shell.append("__devtool_roundtrip_component_file=\"$__devtool_roundtrip_dir/android-live-hud.component\"; ");
        shell.append("test -r \"$__devtool_roundtrip_token_file\"; ");
        shell.append("test -r \"$__devtool_roundtrip_package_file\"; ");
        shell.append("test -r \"$__devtool_roundtrip_component_file\"; ");
        shell.append("__devtool_roundtrip_token=$(cat \"$__devtool_roundtrip_token_file\"); ");
        shell.append("__devtool_roundtrip_package=$(cat \"$__devtool_roundtrip_package_file\"); ");
        shell.append("__devtool_roundtrip_component=$(cat \"$__devtool_roundtrip_component_file\"); ");
        shell.append("test \"$__devtool_roundtrip_token\" = \"$DEVTOOL_ANDROID_HUD_TOKEN\"; ");
        shell.append("test \"$__devtool_roundtrip_package\" = \"$DEVTOOL_ANDROID_HUD_PACKAGE\"; ");
        shell.append("test \"$__devtool_roundtrip_component\" = \"$DEVTOOL_ANDROID_HUD_COMPONENT\"; ");
        shell.append("printf '%s\\n' ").append(marker).append("; ");
        shell.append("/system/bin/am broadcast --user 0 -f 0x20 -n \"$__devtool_roundtrip_component\" ");
        shell.append("-a \"$__devtool_roundtrip_package.UPDATE\" ");
        shell.append("--es token \"$__devtool_roundtrip_token\" --ei protocol_version ")
                .append(BridgeProtocol.VERSION).append(" ");
        shell.append("--es run_id \"$DEVTOOL_RUN_ID\" ");
        shell.append("--es status running --es task 'Bridge roundtrip probe' --es phase 'Roundtrip callback' ");
        shell.append("--es detail 'Probe command callback reached Android' ");
        shell.append("--es trace_checkpoint ").append(checkpoint).append(" ");
        shell.append("--es trace_message 'Probe command callback reached Android' ");
        shell.append("--es wrapper_stage ").append(checkpoint).append(" ");
        shell.append("--ei progress 50 --ei phase_progress 50 --ez indeterminate true ");
        shell.append("--el updated_at_ms $(__devtool_wrapper_now_ms) >/dev/null; ");
        shell.append("printf '%s_DONE\\n' ").append(marker);
        return shell.toString();
    }

    private static boolean has(List<DebugEventRecord> events, String eventType) {
        String wanted = RunCommandTrace.normalizeCheckpoint(eventType);
        for (DebugEventRecord event : events) {
            if (wanted.equals(RunCommandTrace.normalizeCheckpoint(event.eventType))) return true;
        }
        return false;
    }

    private static String checkpoints(boolean wrapperStarted, boolean tokenMarkersOk,
                                      boolean workdirChecked, boolean envReady,
                                      boolean execStarting, boolean callbackReceived,
                                      boolean resultReceived, boolean stdoutMarker) {
        return "wrapper_started=" + wrapperStarted
                + ",token_markers_ok=" + tokenMarkersOk
                + ",workdir_checked=" + workdirChecked
                + ",env_ready=" + envReady
                + ",exec_starting=" + execStarting
                + ",callback_received=" + callbackReceived
                + ",result_received=" + resultReceived
                + ",stdout_marker=" + stdoutMarker;
    }

    private static String failureDetail(boolean wrapperStarted, boolean tokenMarkersOk,
                                        boolean workdirChecked, boolean envReady,
                                        boolean execStarting, boolean callbackReceived,
                                        boolean resultReceived, boolean stdoutMarker,
                                        int exitCode, String stderr, String error) {
        if (!wrapperStarted) return "Intent/result returned, but the Termux wrapper checkpoint never reached Android.";
        if (!tokenMarkersOk) return "Wrapper started, but bridge token or component markers were missing.";
        if (!workdirChecked) return "Wrapper started, but Termux did not confirm the working directory.";
        if (!envReady) return "Wrapper started, but environment readiness was not recorded.";
        if (!execStarting) return "Wrapper never reached the requested probe command.";
        if (!callbackReceived) return "Probe command ran, but its authenticated callback did not reach BridgeReceiver.";
        if (!resultReceived) return "Callback reached Android, but Termux did not return a pending result.";
        if (!stdoutMarker) return "Termux returned, but the roundtrip stdout marker was missing.";
        String cleanError = firstNonEmpty(error, stderr);
        return "Bridge roundtrip failed with exit " + exitCode
                + (cleanError.isEmpty() ? "" : ": " + cleanError);
    }

    private static String firstNonEmpty(String... values) {
        for (String value : values) {
            String clean = value == null ? "" : HudUpdate.plainText(value).trim();
            if (!clean.isEmpty()) return clean.length() > 240 ? clean.substring(0, 237) + "…" : clean;
        }
        return "";
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static void notifyChanged(Context context) {
        context.sendBroadcast(new Intent(ACTION_UPDATED).setPackage(context.getPackageName()));
    }

    private static String shellQuote(String value) {
        String clean = value == null ? "" : value;
        return "'" + clean.replace("'", "'\\''") + "'";
    }
}
