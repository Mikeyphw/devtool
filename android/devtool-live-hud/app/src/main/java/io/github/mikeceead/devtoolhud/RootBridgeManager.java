package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.util.Base64;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

final class RootBridgeManager {
    static final String ACTION_UPDATED = "io.github.mikeceead.devtoolhud.ROOT_BRIDGE_UPDATED";
    static final String MODULE_ID = "devtool_bridge";
    static final String MODULE_ZIP_NAME = "devtool_root_bridge_module.zip";

    private static final String PREFS = "root_bridge_state";
    private static final String KEY_STATE = "state";
    private static final String KEY_DETAIL = "detail";
    private static final String KEY_VERSION = "version";
    private static final String BRIDGE = "/data/adb/modules/devtool_bridge/bin/devtool-root-bridge";
    private static final String BRIDGE_UPDATE = "/data/adb/modules_update/devtool_bridge/bin/devtool-root-bridge";
    private static final String BRIDGE_LEGACY = "/data/adb/devtool-bridge/bin/devtool-root-bridge";
    private static final ExecutorService EXECUTOR = Executors.newCachedThreadPool();
    private static final SecureRandom IDS = new SecureRandom();
    private static volatile Process activeProcess;
    private static volatile String activeRunId = "";
    private static final int MAX_OUTPUT = 64_000;
    private static final long SHORT_COMMAND_TIMEOUT_SECONDS = 12L;
    private static final long CONTROL_COMMAND_TIMEOUT_SECONDS = 120L;

    private RootBridgeManager() {}

    static String state(Context context) {
        return prefs(context).getString(KEY_STATE, "unknown");
    }

    static String detail(Context context) {
        String value = prefs(context).getString(KEY_DETAIL, "");
        if (value != null && !value.trim().isEmpty()) return value.trim();
        switch (state(context)) {
            case "ready": return "Authenticated root execution is available. Rootless Termux mode remains the default fallback.";
            case "unpaired": return "The module is installed. Pair it with this app's secure bridge token.";
            case "outdated": return "The app token changed. Update the root bridge pairing.";
            case "missing": return "Install the optional KernelSU/Magisk module, reboot, then check again.";
            case "disabled": return "The module is installed but disabled. Enable it in your root manager and reboot.";
            case "checking": return "Checking root access and module status.";
            case "denied": return "Grant Devtool Live HUD root access in KernelSU, Magisk, or APatch.";
            case "error": return "The root bridge returned an error. Check the module and root-manager logs.";
            default: return "Optional. Adds root-assisted execution, cancellation, logs, and chroot access.";
        }
    }

    static String stateLabel(Context context) {
        switch (state(context)) {
            case "ready": return "Connected";
            case "unpaired": return "Pairing needed";
            case "outdated": return "Update needed";
            case "missing": return "Not installed";
            case "disabled": return "Disabled";
            case "checking": return "Checking";
            case "denied": return "Root denied";
            case "error": return "Needs attention";
            default: return "Optional";
        }
    }

    static String actionLabel(Context context) {
        switch (state(context)) {
            case "ready": return "Verify";
            case "unpaired": return "Pair root bridge";
            case "outdated": return "Update pairing";
            case "missing": return "Export module ZIP";
            case "disabled": return "Check again";
            case "checking": return "Working…";
            case "denied": return "Check root access";
            case "error": return "Retry";
            default: return "Check availability";
        }
    }

    static boolean actionEnabled(Context context) {
        return !"checking".equals(state(context));
    }

    static boolean isReady(Context context) {
        return "ready".equals(state(context));
    }

    static String version(Context context) {
        return prefs(context).getString(KEY_VERSION, "");
    }

    static void refresh(Context context) {
        Context app = context.getApplicationContext();
        setState(app, "checking", "", "");
        EXECUTOR.execute(() -> {
            String token = BridgeSecrets.getOrCreateToken(app);
            CommandResult result = rootCommand(bridgeInvocation("status " + shellQuote(token)), SHORT_COMMAND_TIMEOUT_SECONDS);
            if (result.exitCode == 0 && result.output.contains("\"installed\":true")) {
                boolean enabled = result.output.contains("\"enabled\":true");
                if (!enabled) {
                    setState(app, "disabled", "The module is installed but disabled in the root manager.", jsonValue(result.output, "version"));
                    return;
                }
                boolean paired = result.output.contains("\"paired\":true");
                boolean matches = result.output.contains("\"matches\":true");
                String version = jsonValue(result.output, "version");
                String state = !paired ? "unpaired" : (matches ? "ready" : "outdated");
                String detail = state.equals("ready")
                        ? "Root bridge " + (version.isEmpty() ? "" : version + " ") + "is paired and ready."
                        : (state.equals("outdated") ? "The installed root pairing does not match the current app token."
                        : "Module detected. Pair it with the app token to enable execution.");
                setState(app, state, detail, version);
            } else if (result.output.contains("DEVTOOL_ROOT_MODULE_MISSING")) {
                setState(app, "missing", "The optional root bridge module is not installed.", "");
            } else if (result.exitCode == 126 || result.output.toLowerCase(Locale.ROOT).contains("permission denied")
                    || result.output.toLowerCase(Locale.ROOT).contains("cannot run program \"su\"")
                    || result.output.toLowerCase(Locale.ROOT).contains("not allowed")) {
                setState(app, "denied", compact(result.output, "Root permission was denied"), "");
            } else if (result.exitCode == 127) {
                setState(app, "denied", compact(result.output, "Root access is unavailable"), "");
            } else {
                String lower = result.output.toLowerCase(Locale.ROOT);
                if (lower.contains("not found") || lower.contains("no such file")) {
                    setState(app, "missing", "The optional root bridge module is not installed.", "");
                } else {
                    setState(app, "error", compact(result.output, "Unable to query the root bridge"), "");
                }
            }
        });
    }

    static void pair(Context context) {
        Context app = context.getApplicationContext();
        setState(app, "checking", "Pairing the root bridge.", "");
        String token = BridgeSecrets.getOrCreateToken(app);
        EXECUTOR.execute(() -> {
            CommandResult result = rootCommand(bridgeInvocation("pair " + shellQuote(token)), SHORT_COMMAND_TIMEOUT_SECONDS);
            if (result.exitCode == 0 && result.output.contains("DEVTOOL_ROOT_BRIDGE_PAIRED")) {
                refresh(app);
            } else {
                setState(app, result.output.toLowerCase(Locale.ROOT).contains("permission denied") ? "denied" : "error",
                        compact(result.output, "Root bridge pairing failed"), "");
            }
        });
    }

    static TermuxCommandRunner.LaunchResult run(Context context, String command, String workdir,
                                                 boolean overlay) {
        return run(context, command, workdir, overlay, null);
    }

    static TermuxCommandRunner.LaunchResult run(Context context, String command, String workdir,
                                                 boolean overlay, Intent taskerFireIntent) {
        String cleanCommand = command == null ? "" : command.trim();
        if (cleanCommand.isEmpty()) return rejectRun(context, "ROOT_RUN_REJECTED", "Command is empty");
        if (!isReady(context)) return rejectRun(context, "ROOT_BRIDGE_NOT_CONNECTED", "Optional root bridge is not connected");
        if (!isAllowedDevtoolCommand(cleanCommand)) {
            return rejectRun(context, "ROOT_COMMAND_REJECTED", "Root bridge only accepts generated devtool or devtool-androidos commands");
        }
        RunRecord active = RunStore.active(context);
        if (active != null) {
            DebugRecorder.warning(context, "root_bridge", "ROOT_RUN_REJECTED_ACTIVE_RUN", "RootBridgeManager",
                    active.runId, active.repositoryId, "", "", "A devtool run is already active",
                    DebugRecorder.details("active_status", active.status, "active_phase", active.phase));
            return new TermuxCommandRunner.LaunchResult(false, -1,
                    "A devtool run is already active. Finish or cancel it before starting another.");
        }

        int executionId = nextExecutionId();
        String runId = "root-" + Integer.toUnsignedString(executionId, 16);
        String token = BridgeSecrets.getOrCreateToken(context);
        String cleanWorkdir = workdir == null || workdir.trim().isEmpty() ? "~/" : workdir.trim();
        String workdir64 = Base64.encodeToString(cleanWorkdir.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
        String command64 = Base64.encodeToString(cleanCommand.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
        String shell = bridgeInvocation("run " + shellQuote(token) + " " + shellQuote(runId) + " "
                + (overlay ? "1" : "0") + " " + shellQuote(workdir64) + " " + shellQuote(command64));

        Context app = context.getApplicationContext();
        RunCommandTrace.record(app, RunCommandTrace.COMMAND_PLAN_BUILT, "RootBridgeManager",
                runId, "Command plan built for root bridge",
                DebugRecorder.details("workdir", cleanWorkdir, "overlay", overlay,
                        "command_length", cleanCommand.length()));
        RunCommandTrace.record(app, RunCommandTrace.COMMAND_ENVELOPE_WRITTEN, "RootBridgeManager",
                runId, "Root bridge command envelope prepared",
                DebugRecorder.details("workdir", cleanWorkdir, "overlay", overlay));
        HudUpdate started = HudUpdate.commandStarted(runId, cleanCommand, cleanWorkdir, overlay);
        RunStore.started(app, runId, cleanCommand, cleanWorkdir);
        RunCommandTrace.record(app, RunCommandTrace.RUNSTORE_CREATED, "RootBridgeManager",
                runId, "RunStore created root bridge launch record",
                DebugRecorder.details("workdir", cleanWorkdir, "overlay", overlay));
        new NotificationController(app).post(started);
        OverlayHudService.publish(app, started);

        Intent taskerIntent = taskerFireIntent == null ? null : new Intent(taskerFireIntent);
        RunCommandTrace.record(app, RunCommandTrace.ROOT_BRIDGE_DISPATCHED, "RootBridgeManager",
                runId, "Root bridge execution dispatched",
                DebugRecorder.details("execution_id", executionId, "overlay", overlay));
        EXECUTOR.execute(() -> executeRun(app, runId, executionId, shell, overlay, taskerIntent));
        return new TermuxCommandRunner.LaunchResult(true, executionId, "");
    }

    static TermuxCommandRunner.LaunchResult runValidationControl(Context context, String command,
                                                                  String workdir, String operation) {
        String cleanCommand = command == null ? "" : command.trim();
        if (cleanCommand.isEmpty()) return rejectRun(context, "ROOT_VALIDATION_REJECTED", "Command is empty");
        if (!isReady(context)) return rejectRun(context, "ROOT_BRIDGE_NOT_CONNECTED", "Optional root bridge is not connected");
        if (!isAllowedDevtoolCommand(cleanCommand)) {
            return rejectRun(context, "ROOT_VALIDATION_REJECTED", "Root validation control only accepts generated devtool commands");
        }
        int executionId = nextExecutionId();
        Context app = context.getApplicationContext();
        String cleanWorkdir = workdir == null || workdir.trim().isEmpty() ? "/" : workdir.trim();
        EXECUTOR.execute(() -> {
            String shell = "cd " + shellQuote(cleanWorkdir) + " && " + cleanCommand;
            CommandResult result = rootCommand(shell, CONTROL_COMMAND_TIMEOUT_SECONDS);
            ValidationController.handleResult(app, operation, result.exitCode, result.output, "", "", "root-bridge");
        });
        return new TermuxCommandRunner.LaunchResult(true, executionId, "");
    }

    static boolean cancel(Context context, String runId) {
        if (runId == null || !runId.startsWith("root-")) return false;
        Process process = activeProcess;
        if (process != null && runId.equals(activeRunId)) {
            process.destroy();
            activeProcess = null;
            activeRunId = "";
        }
        String token = BridgeSecrets.getOrCreateToken(context);
        EXECUTOR.execute(() -> rootCommand(bridgeInvocation("cancel " + shellQuote(token) + " " + shellQuote(runId)),
                SHORT_COMMAND_TIMEOUT_SECONDS));
        return true;
    }

    private static void executeRun(Context context, String runId, int executionId,
                                   String shell, boolean overlay, Intent taskerFireIntent) {
        StringBuilder output = new StringBuilder();
        int exitCode = -1;
        String error = "";
        try {
            Process process = new ProcessBuilder("su", "-c", shell).redirectErrorStream(true).start();
            RunCommandTrace.record(context, RunCommandTrace.ROOT_BRIDGE_PROCESS_STARTED, "RootBridgeManager",
                    runId, "Root bridge process started", DebugRecorder.details("execution_id", executionId));
            activeProcess = process;
            activeRunId = runId;
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                    process.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    appendLimited(output, line + "\n");
                }
            }
            exitCode = process.waitFor();
        } catch (Exception exception) {
            error = exception.getMessage() == null ? exception.getClass().getSimpleName() : exception.getMessage();
        } finally {
            if (runId.equals(activeRunId)) {
                activeProcess = null;
                activeRunId = "";
            }
        }
        String stdout = output.toString();
        boolean richCompletion = RunStore.finish(context, runId, exitCode, stdout, "", error);
        if (!richCompletion) {
            HudUpdate completed = HudUpdate.commandFinished(runId, exitCode, stdout, "", error, overlay);
            new NotificationController(context).post(completed);
            OverlayHudService.publish(context, completed);
        }
        if (taskerFireIntent != null) {
            Bundle variables = TaskerProtocol.variables(executionId, exitCode, stdout, "", error);
            int resultCode = exitCode == 0 && error.isEmpty()
                    ? TaskerProtocol.RESULT_OK : TaskerProtocol.RESULT_FAILED;
            TaskerProtocol.signalFinish(context, taskerFireIntent, resultCode, variables);
        }
        context.sendBroadcast(new Intent(ACTION_UPDATED).setPackage(context.getPackageName())
                .putExtra("run_id", runId).putExtra("execution_id", executionId));
    }

    private static CommandResult rootCommand(String shell) {
        return rootCommand(shell, SHORT_COMMAND_TIMEOUT_SECONDS);
    }

    private static CommandResult rootCommand(String shell, long timeoutSeconds) {
        Process process = null;
        Thread readerThread = null;
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        AtomicBoolean truncated = new AtomicBoolean(false);
        try {
            process = new ProcessBuilder("su", "-c", shell).redirectErrorStream(true).start();
            Process runningProcess = process;
            readerThread = new Thread(() -> copyLimited(runningProcess.getInputStream(), output, truncated),
                    "devtool-root-command-reader");
            readerThread.start();
            long safeTimeout = Math.max(1L, timeoutSeconds);
            boolean completed = false;
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(safeTimeout);
            while (System.nanoTime() < deadline) {
                if (process.waitFor(100L, TimeUnit.MILLISECONDS)) {
                    completed = true;
                    break;
                }
            }
            if (!completed) {
                process.destroy();
                if (!process.waitFor(1L, TimeUnit.SECONDS)) process.destroyForcibly();
                if (readerThread != null) readerThread.join(1_000L);
                String message = "Root bridge command timed out after " + safeTimeout + "s";
                String captured = outputText(output);
                return new CommandResult(124, captured.isEmpty() ? message : message + "\n" + captured);
            }
            if (readerThread != null) readerThread.join(1_000L);
            String captured = outputText(output);
            if (truncated.get()) captured = "[output truncated]\n" + captured;
            return new CommandResult(process.exitValue(), captured);
        } catch (Exception exception) {
            if (process != null) process.destroy();
            return new CommandResult(127, exception.getMessage() == null
                    ? exception.getClass().getSimpleName() : exception.getMessage());
        }
    }

    private static String bridgeInvocation(String arguments) {
        return "if [ -x " + shellQuote(BRIDGE) + " ]; then " + shellQuote(BRIDGE) + " "
                + arguments + "; elif [ -x " + shellQuote(BRIDGE_UPDATE) + " ]; then "
                + shellQuote(BRIDGE_UPDATE) + " " + arguments + "; elif [ -x "
                + shellQuote(BRIDGE_LEGACY) + " ]; then " + shellQuote(BRIDGE_LEGACY) + " "
                + arguments + "; else echo DEVTOOL_ROOT_MODULE_MISSING; exit 127; fi";
    }

    private static boolean isAllowedDevtoolCommand(String command) {
        String compact = command.replace('\n', ' ').replaceAll("\\s+", " ").trim();
        return !containsShellControlSyntax(command)
                && compact.matches("(?:[A-Za-z_][A-Za-z0-9_]*=[^ ]+ )*(?:devtool|devtool-androidos)(?: |$).*");
    }

    private static boolean containsShellControlSyntax(String command) {
        boolean singleQuoted = false;
        boolean doubleQuoted = false;
        boolean escaped = false;
        for (int i = 0; i < command.length(); i++) {
            char c = command.charAt(i);
            if (escaped) {
                escaped = false;
                continue;
            }
            if (c == '\\' && !singleQuoted) {
                escaped = true;
                continue;
            }
            if (c == '\'' && !doubleQuoted) {
                singleQuoted = !singleQuoted;
                continue;
            }
            if (c == '"' && !singleQuoted) {
                doubleQuoted = !doubleQuoted;
                continue;
            }
            if (singleQuoted || doubleQuoted) continue;
            if (c == '\n' || c == '\r' || c == ';' || c == '`'
                    || c == '<' || c == '>' || c == '|' || c == '&') {
                return true;
            }
            if (c == '$' && i + 1 < command.length() && command.charAt(i + 1) == '(') {
                return true;
            }
        }
        return singleQuoted || doubleQuoted || escaped;
    }

    private static void setState(Context context, String state, String detail, String version) {
        SharedPreferences.Editor editor = prefs(context).edit().putString(KEY_STATE, state);
        if (detail != null) editor.putString(KEY_DETAIL, detail);
        if (version != null && !version.isEmpty()) editor.putString(KEY_VERSION, version);
        editor.apply();
        DebugRecorder.record(context, state.equals("ready") ? "success"
                        : state.equals("checking") || state.equals("unknown") ? "info" : "warning",
                "root_bridge", "ROOT_BRIDGE_STATE_CHANGED", "RootBridgeManager", "", "", "", "",
                detail == null || detail.trim().isEmpty() ? "Root bridge state changed: " + state : detail,
                DebugRecorder.details("state", state, "version", version));
        context.sendBroadcast(new Intent(ACTION_UPDATED).setPackage(context.getPackageName()));
    }

    private static TermuxCommandRunner.LaunchResult rejectRun(Context context, String eventType, String reason) {
        DebugRecorder.warning(context, "root_bridge", eventType, "RootBridgeManager", "", "", "", "", reason, "{}");
        return new TermuxCommandRunner.LaunchResult(false, -1, reason);
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static String jsonValue(String json, String key) {
        String marker = "\"" + key + "\":\"";
        int start = json.indexOf(marker);
        if (start < 0) return "";
        start += marker.length();
        int end = json.indexOf('"', start);
        return end < 0 ? "" : json.substring(start, end);
    }

    private static String compact(String value, String fallback) {
        String clean = value == null ? "" : value.trim().replace('\n', ' ').replace('\r', ' ');
        if (clean.isEmpty()) clean = fallback;
        return clean.length() > 260 ? clean.substring(0, 257) + "…" : clean;
    }

    private static String shellQuote(String value) {
        String clean = value == null ? "" : value;
        return "'" + clean.replace("'", "'\\''") + "'";
    }

    private static int nextExecutionId() {
        int value;
        do value = IDS.nextInt(Integer.MAX_VALUE);
        while (value == 0);
        return value;
    }

    private static void appendLimited(StringBuilder target, String value) {
        target.append(value);
        if (target.length() > MAX_OUTPUT) target.delete(0, target.length() - MAX_OUTPUT);
    }

    private static void copyLimited(InputStream input, ByteArrayOutputStream output, AtomicBoolean truncated) {
        byte[] buffer = new byte[4096];
        try {
            int read;
            while ((read = input.read(buffer)) != -1) {
                output.write(buffer, 0, read);
                if (output.size() > MAX_OUTPUT) {
                    byte[] bytes = output.toByteArray();
                    output.reset();
                    output.write(bytes, bytes.length - MAX_OUTPUT, MAX_OUTPUT);
                    truncated.set(true);
                }
            }
        } catch (Exception ignored) {
        }
    }

    private static String outputText(ByteArrayOutputStream output) {
        return new String(output.toByteArray(), StandardCharsets.UTF_8);
    }

    private static final class CommandResult {
        final int exitCode;
        final String output;

        CommandResult(int exitCode, String output) {
            this.exitCode = exitCode;
            this.output = output == null ? "" : output;
        }
    }
}
