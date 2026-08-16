package io.github.mikeceead.devtoolhud;

import android.annotation.SuppressLint;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;

import java.security.SecureRandom;

@SuppressLint("SdCardPath")
final class TermuxCommandRunner {
    static final String TERMUX_PACKAGE = "com.termux";
    static final String RUN_COMMAND_PERMISSION = "com.termux.permission.RUN_COMMAND";
    private static final String SERVICE = "com.termux.app.RunCommandService";
    private static final String ACTION = "com.termux.RUN_COMMAND";
    private static final String EXTRA_PATH = "com.termux.RUN_COMMAND_PATH";
    private static final String EXTRA_ARGUMENTS = "com.termux.RUN_COMMAND_ARGUMENTS";
    private static final String EXTRA_WORKDIR = "com.termux.RUN_COMMAND_WORKDIR";
    private static final String EXTRA_BACKGROUND = "com.termux.RUN_COMMAND_BACKGROUND";
    private static final String EXTRA_PENDING_INTENT = "com.termux.RUN_COMMAND_PENDING_INTENT";
    private static final String EXTRA_LABEL = "com.termux.RUN_COMMAND_COMMAND_LABEL";
    private static final String EXTRA_DESCRIPTION = "com.termux.RUN_COMMAND_COMMAND_DESCRIPTION";
    private static final String TERMUX_PREFIX = "/data/data/com.termux/files/usr";
    private static final SecureRandom IDS = new SecureRandom();
    private static final String TAG = "DevtoolTermuxLaunch";
    private static final long PROGRESS_WATCHDOG_MS = 7000L;

    static final class LaunchResult {
        final boolean started;
        final int executionId;
        final String error;

        LaunchResult(boolean started, int executionId, String error) {
            this.started = started;
            this.executionId = executionId;
            this.error = error;
        }
    }

    private TermuxCommandRunner() {}

    static boolean hasPermission(Context context) {
        return context.checkSelfPermission(RUN_COMMAND_PERMISSION) == PackageManager.PERMISSION_GRANTED;
    }

    static boolean isInstalled(Context context) {
        try {
            context.getPackageManager().getPackageInfo(TERMUX_PACKAGE, 0);
            return true;
        } catch (PackageManager.NameNotFoundException ignored) {
            return false;
        }
    }

    static LaunchResult run(Context context, String command, String workdir, boolean background,
                            boolean overlay, Intent taskerFireIntent) {
        return runInternal(context, command, workdir, background, overlay, taskerFireIntent, "", "");
    }

    static LaunchResult runBridgeOperation(Context context, String command,
                                           String operation, String expectedHash) {
        return runInternal(context, command, TermuxPaths.HOME,
                true, false, null, operation, expectedHash);
    }

    static LaunchResult runUtilityCommand(Context context, String command, String workdir,
                                          boolean background, String operation) {
        return runInternal(context, command, workdir, background, false, null,
                operation == null ? "repository-utility" : operation, "");
    }

    private static LaunchResult runInternal(Context context, String command, String workdir,
                                            boolean background, boolean overlay, Intent taskerFireIntent,
                                            String bridgeOperation, String bridgeExpectedHash) {
        String cleanCommand = command == null ? "" : command.trim();
        if (cleanCommand.isEmpty()) return reject(context, "RUN_COMMAND_REJECTED", "Command is empty");
        if (cleanCommand.length() > 120_000) return reject(context, "RUN_COMMAND_REJECTED", "Command is too long");
        if (!isInstalled(context)) return reject(context, "TERMUX_MISSING", "Termux is not installed");
        if (!hasPermission(context)) return reject(context, "TERMUX_RUN_COMMAND_PERMISSION_MISSING", "Run commands in Termux permission is not granted");
        if (bridgeOperation == null || bridgeOperation.isEmpty()) {
            RunRecord active = RunStore.active(context);
            if (active != null) {
                DebugRecorder.warning(context, "run", "RUN_COMMAND_REJECTED_ACTIVE_RUN", "TermuxCommandRunner",
                        active.runId, active.repositoryId, "", "",
                        "A devtool run is already active", DebugRecorder.details("active_status", active.status, "active_phase", active.phase));
                return new LaunchResult(false, -1, "A devtool run is already active. Finish or cancel it before starting another.");
            }
        }

        int executionId = nextExecutionId();
        String runId = "termux-" + executionId;
        Intent resultIntent = new Intent(context, TermuxResultService.class)
                .putExtra(TermuxResultService.EXTRA_RUN_ID, runId)
                .putExtra(TermuxResultService.EXTRA_EXECUTION_ID, executionId)
                .putExtra(TermuxResultService.EXTRA_TASKER_FIRE_INTENT, taskerFireIntent)
                .putExtra(TermuxResultService.EXTRA_OVERLAY, overlay)
                .putExtra(TermuxResultService.EXTRA_BRIDGE_OPERATION, bridgeOperation)
                .putExtra(TermuxResultService.EXTRA_BRIDGE_EXPECTED_HASH, bridgeExpectedHash);
        int flags = PendingIntent.FLAG_ONE_SHOT | PendingIntent.FLAG_CANCEL_CURRENT;
        flags |= Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0;
        PendingIntent result = PendingIntent.getService(context, executionId, resultIntent, flags);

        String normalizedWorkdir = TermuxPaths.normalizeWorkdir(workdir);
        String shell = resolveShell();
        String category = bridgeOperation == null || bridgeOperation.isEmpty() ? "run" : "bridge";
        DebugRecorder.info(context, category, "RUN_COMMAND_REQUESTED", "TermuxCommandRunner",
                runId, "", "", "", "Run command request built",
                DebugRecorder.details("workdir", normalizedWorkdir, "background", background,
                        "shell", shell, "command_length", cleanCommand.length(),
                        "bridge_operation", bridgeOperation));
        RunCommandTrace.record(context, RunCommandTrace.COMMAND_PLAN_BUILT, "TermuxCommandRunner",
                runId, "Command plan built for Termux RUN_COMMAND",
                DebugRecorder.details("workdir", normalizedWorkdir, "background", background,
                        "shell", shell, "command_length", cleanCommand.length(),
                        "bridge_operation", bridgeOperation));
        String primaryCommand = primaryCommand(cleanCommand);
        String[] arguments = shellArguments(context, runId, cleanCommand, overlay, primaryCommand);
        RunCommandTrace.record(context, RunCommandTrace.COMMAND_ENVELOPE_WRITTEN, "TermuxCommandRunner",
                runId, "Shell command envelope prepared",
                DebugRecorder.details("workdir", normalizedWorkdir, "shell", shell,
                        "argument_count", arguments.length, "overlay", overlay,
                        "primary_command", primaryCommand));
        Intent request = new Intent()
                .setClassName(TERMUX_PACKAGE, SERVICE)
                .setAction(ACTION)
                .putExtra(EXTRA_PATH, shell)
                .putExtra(EXTRA_ARGUMENTS, arguments)
                .putExtra(EXTRA_WORKDIR, normalizedWorkdir)
                .putExtra(EXTRA_BACKGROUND, background)
                .putExtra(EXTRA_PENDING_INTENT, result)
                .putExtra(EXTRA_LABEL, "Devtool Frontend")
                .putExtra(EXTRA_DESCRIPTION, "Runs a user-configured devtool command and returns its result.");
        RunCommandTrace.record(context, RunCommandTrace.TERMUX_INTENT_BUILT, "TermuxCommandRunner",
                runId, "Termux RUN_COMMAND intent built",
                DebugRecorder.details("workdir", normalizedWorkdir, "background", background, "shell", shell));
        try {
            DebugRecorder.info(context, category, "TERMUX_INTENT_SENT", "TermuxCommandRunner",
                    runId, "", "", "", "Termux RUN_COMMAND intent sent",
                    DebugRecorder.details("workdir", normalizedWorkdir, "background", background, "shell", shell));
            RunCommandTrace.record(context, RunCommandTrace.TERMUX_INTENT_SENT, "TermuxCommandRunner",
                    runId, "Termux RUN_COMMAND intent sent",
                    DebugRecorder.details("workdir", normalizedWorkdir, "background", background, "shell", shell));
            context.startService(request);
            DebugRecorder.info(context, category, "TERMUX_INTENT_ACCEPTED", "TermuxCommandRunner",
                    runId, "", "", "", "Termux RUN_COMMAND service accepted request",
                    DebugRecorder.details("workdir", normalizedWorkdir, "background", background, "shell", shell));
            RunCommandTrace.record(context, RunCommandTrace.TERMUX_RUNCOMMAND_ACCEPTED, "TermuxCommandRunner",
                    runId, "Termux RUN_COMMAND service accepted request",
                    DebugRecorder.details("workdir", normalizedWorkdir, "background", background, "shell", shell));
            AndroidPrivacyLog.info(TAG, "RunCommand accepted run=" + runId + " mode=" + (background ? "background" : "foreground")
                    + " shell=" + shell + " workdir=" + normalizedWorkdir);
            if (!background && (bridgeOperation == null || bridgeOperation.isEmpty()
                    || bridgeOperation.equals("repository-shell"))) {
                new Handler(Looper.getMainLooper()).postDelayed(() -> openTermux(context), 250L);
            }
            if (bridgeOperation == null || bridgeOperation.isEmpty()) {
                HudUpdate started = HudUpdate.commandStarted(runId, cleanCommand, normalizedWorkdir, overlay);
                RunStore.started(context, runId, cleanCommand, normalizedWorkdir, background, shell);
                RunCommandTrace.record(context, RunCommandTrace.RUNSTORE_CREATED, "TermuxCommandRunner",
                        runId, "RunStore created launch record",
                        DebugRecorder.details("workdir", normalizedWorkdir, "background", background, "shell", shell));
                new NotificationController(context).post(started);
                OverlayHudService.publish(context, started);
                scheduleProgressWatchdog(context, runId, cleanCommand, normalizedWorkdir, background, overlay, started.startedAtMs);
            }
            return new LaunchResult(true, executionId, "");
        } catch (RuntimeException error) {
            String message = error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage();
            DebugRecorder.error(context, category, "TERMUX_INTENT_FAILED", "TermuxCommandRunner",
                    runId, "", "", "", message,
                    DebugRecorder.details("workdir", normalizedWorkdir, "background", background, "shell", shell));
            AndroidPrivacyLog.error(TAG, "Could not start RunCommand run=" + runId, error);
            return new LaunchResult(false, executionId, message);
        }
    }

    private static LaunchResult reject(Context context, String eventType, String reason) {
        DebugRecorder.error(context, "run", eventType, "TermuxCommandRunner", "", "", "", "", reason, "{}");
        return new LaunchResult(false, -1, reason);
    }

    static boolean openTermux(Context context) {
        Intent launch = context.getPackageManager().getLaunchIntentForPackage(TERMUX_PACKAGE);
        if (launch == null) return false;
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        try {
            context.startActivity(launch);
            return true;
        } catch (RuntimeException error) {
            AndroidPrivacyLog.warn(TAG, "Could not bring Termux to foreground", error);
            return false;
        }
    }

    private static void scheduleProgressWatchdog(
            Context context, String runId, String command, String workdir,
            boolean background, boolean overlay, long startedAtMs) {
        Context app = context.getApplicationContext();
        new Handler(Looper.getMainLooper()).postDelayed(() -> {
            if (!RunStore.stillPreparing(app, runId)) return;
            String message = background
                    ? "No live bridge update after 7 seconds; the command may still be running in Termux background mode"
                    : "No live bridge update after 7 seconds; check the visible Termux session";
            DebugRecorder.warning(app, "run", "RUN_PROGRESS_WATCHDOG_WAITING", "TermuxCommandRunner",
                    runId, "", "", "", message,
                    DebugRecorder.details("workdir", workdir, "background", background));
            RunCommandTrace.record(app, RunCommandTrace.RUN_PROGRESS_WATCHDOG_WAITING, "TermuxCommandRunner",
                    runId, message, DebugRecorder.details("workdir", workdir, "background", background));
            RunStore.appendDiagnostic(app, runId, message);
            HudUpdate waiting = HudUpdate.commandWaiting(
                    runId, command, workdir, background, overlay, startedAtMs);
            RunStore.update(app, waiting);
            new NotificationController(app).post(waiting);
            OverlayHudService.publish(app, waiting);
        }, PROGRESS_WATCHDOG_MS);
    }

    private static String buildShellCommand(Context context, String runId, String command,
                                            boolean overlay, String selectedShell,
                                            String primaryCommand) {
        StringBuilder shell = new StringBuilder();
        shell.append("export DEVTOOL_RUN_ID=").append(shellQuote(runId)).append("; ");
        String token = BridgeSecrets.getOrCreateToken(context);
        shell.append("export DEVTOOL_ANDROID_HUD_TOKEN=")
                .append(shellQuote(token)).append("; ");
        shell.append("export DEVTOOL_HUD_OVERLAY=").append(overlay ? "1" : "0").append("; ");
        shell.append("export DEVTOOL_ANDROID_HUD_AUTOSTART=1; ");
        String bridgePackage = context.getPackageName();
        String bridgeComponent = bridgePackage + "/" + BridgeReceiver.class.getName();
        String bridgeAction = bridgePackage + ".UPDATE";
        shell.append("export DEVTOOL_ANDROID_HUD_PACKAGE=")
                .append(shellQuote(bridgePackage)).append("; ");
        shell.append("export DEVTOOL_ANDROID_HUD_COMPONENT=")
                .append(shellQuote(bridgeComponent)).append("; ");
        shell.append("export DEVTOOL_ANDROID_HUD_ACTION_PREFIX=")
                .append(shellQuote(bridgePackage)).append("; ");
        // The wrapper emits authenticated bridge extras named trace_checkpoint and
        // trace_message, including RunCommandTrace.TERMUX_SHELL_STARTED for
        // backwards-compatible launch trace ordering.
        shell.append(TermuxWrapperSentinel.wrapperPreamble(bridgeComponent, bridgeAction,
                runId, token, primaryCommand));
        shell.append(TermuxWrapperSentinel.wrapperChecks(selectedShell));
        // Backwards source contract note: this still appends the original command
        // without precreating public storage log directories. Equivalent to
        // shell.append(command), now wrapped so the exit code sentinel can run.
        // Legacy bridge detail: "Termux shell started; waiting for devtool progress".
        shell.append("( ").append(command).append(" )");
        shell.append(TermuxWrapperSentinel.exitReporter());
        return shell.toString();
    }

    private static String resolveShell() {
        String[] candidates = new String[]{
                TERMUX_PREFIX + "/bin/zsh",
                TERMUX_PREFIX + "/bin/sh",
                "/system/bin/sh"
        };
        for (String candidate : candidates) {
            if (new java.io.File(candidate).canExecute()) return candidate;
        }
        return TERMUX_PREFIX + "/bin/sh";
    }

    private static String[] shellArguments(Context context, String runId, String command,
                                           boolean overlay, String primaryCommand) {
        String shell = resolveShell();
        String script = buildShellCommand(context, runId, command, overlay, shell, primaryCommand);
        if (shell.endsWith("/zsh")) return new String[]{"-lc", script};
        return new String[]{"-c", script};
    }

    private static String primaryCommand(String command) {
        String clean = command == null ? "" : command.trim();
        if (clean.isEmpty()) return "";
        String lower = clean.toLowerCase(java.util.Locale.ROOT);
        String[] known = new String[]{
                "devtool-androidos", "devtool", "git", "python3", "python",
                "bash", "zsh", "sh", "chrootctl", "su", "runuser"
        };
        for (String candidate : known) {
            if (lower.equals(candidate) || lower.startsWith(candidate + " ")
                    || lower.contains(" " + candidate + " ")) {
                return candidate;
            }
        }
        int firstSpace = clean.indexOf(' ');
        String first = firstSpace < 0 ? clean : clean.substring(0, firstSpace);
        if (first.matches("[A-Za-z0-9_./:@%+=,-]+") && !first.contains("=")) return first;
        return "";
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

}
