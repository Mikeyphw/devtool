package io.github.mikeceead.devtoolhud;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import java.security.SecureRandom;

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
        return Build.VERSION.SDK_INT < 23
                || context.checkSelfPermission(RUN_COMMAND_PERMISSION) == PackageManager.PERMISSION_GRANTED;
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

    private static LaunchResult runInternal(Context context, String command, String workdir,
                                            boolean background, boolean overlay, Intent taskerFireIntent,
                                            String bridgeOperation, String bridgeExpectedHash) {
        String cleanCommand = command == null ? "" : command.trim();
        if (cleanCommand.isEmpty()) return new LaunchResult(false, -1, "Command is empty");
        if (cleanCommand.length() > 120_000) return new LaunchResult(false, -1, "Command is too long");
        if (!isInstalled(context)) return new LaunchResult(false, -1, "Termux is not installed");
        if (!hasPermission(context)) return new LaunchResult(false, -1, "Run commands in Termux permission is not granted");
        if (bridgeOperation == null || bridgeOperation.isEmpty()) {
            RunRecord active = RunStore.active(context);
            if (active != null) {
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

        Intent request = new Intent()
                .setClassName(TERMUX_PACKAGE, SERVICE)
                .setAction(ACTION)
                .putExtra(EXTRA_PATH, resolveShell())
                .putExtra(EXTRA_ARGUMENTS, shellArguments(context, runId, cleanCommand, overlay))
                .putExtra(EXTRA_WORKDIR, TermuxPaths.normalizeWorkdir(workdir))
                .putExtra(EXTRA_BACKGROUND, background)
                .putExtra(EXTRA_PENDING_INTENT, result)
                .putExtra(EXTRA_LABEL, "Devtool Frontend")
                .putExtra(EXTRA_DESCRIPTION, "Runs a user-configured devtool command and returns its result.");
        try {
            context.startService(request);
            Log.i(TAG, "RunCommand accepted run=" + runId + " mode=" + (background ? "background" : "foreground")
                    + " shell=" + resolveShell() + " workdir=" + TermuxPaths.normalizeWorkdir(workdir));
            if (!background && (bridgeOperation == null || bridgeOperation.isEmpty())) {
                new Handler(Looper.getMainLooper()).postDelayed(() -> openTermux(context), 250L);
            }
            if (bridgeOperation == null || bridgeOperation.isEmpty()) {
                String normalizedWorkdir = TermuxPaths.normalizeWorkdir(workdir);
                HudUpdate started = HudUpdate.commandStarted(runId, cleanCommand, normalizedWorkdir, overlay);
                RunStore.started(context, runId, cleanCommand, normalizedWorkdir, background, resolveShell());
                new NotificationController(context).post(started);
                OverlayHudService.publish(context, started);
                scheduleProgressWatchdog(context, runId, cleanCommand, normalizedWorkdir, background, overlay, started.startedAtMs);
            }
            return new LaunchResult(true, executionId, "");
        } catch (RuntimeException error) {
            Log.e(TAG, "Could not start RunCommand run=" + runId, error);
            return new LaunchResult(false, executionId,
                    error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage());
        }
    }

    static boolean openTermux(Context context) {
        Intent launch = context.getPackageManager().getLaunchIntentForPackage(TERMUX_PACKAGE);
        if (launch == null) return false;
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        try {
            context.startActivity(launch);
            return true;
        } catch (RuntimeException error) {
            Log.w(TAG, "Could not bring Termux to foreground", error);
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
            RunStore.appendDiagnostic(app, runId, message);
            HudUpdate waiting = HudUpdate.commandWaiting(
                    runId, command, workdir, background, overlay, startedAtMs);
            RunStore.update(app, waiting);
            new NotificationController(app).post(waiting);
            OverlayHudService.publish(app, waiting);
        }, PROGRESS_WATCHDOG_MS);
    }

    private static String buildShellCommand(Context context, String runId, String command, boolean overlay) {
        StringBuilder shell = new StringBuilder();
        shell.append("export DEVTOOL_RUN_ID=").append(shellQuote(runId)).append("; ");
        shell.append("export DEVTOOL_ANDROID_HUD_TOKEN=")
                .append(shellQuote(BridgeSecrets.getOrCreateToken(context))).append("; ");
        shell.append("export DEVTOOL_HUD_OVERLAY=").append(overlay ? "1" : "0").append("; ");
        shell.append("export DEVTOOL_ANDROID_HUD_AUTOSTART=1; ");
        String bridgePackage = context.getPackageName();
        shell.append("export DEVTOOL_ANDROID_HUD_PACKAGE=")
                .append(shellQuote(bridgePackage)).append("; ");
        // A shell-start acknowledgement distinguishes a command that has not
        // launched from one whose devtool bridge is still warming up.
        shell.append("/system/bin/am broadcast --user 0 -n ")
                .append(shellQuote(bridgePackage + "/.BridgeReceiver"))
                .append(" -a ").append(shellQuote(bridgePackage + ".UPDATE"))
                .append(" --es token ").append(shellQuote(BridgeSecrets.getOrCreateToken(context)))
                .append(" --es run_id ").append(shellQuote(runId))
                .append(" --es status waiting --es task ").append(shellQuote("Starting Devtool"))
                .append(" --es phase Termux --es detail ").append(shellQuote("Termux shell started; waiting for devtool progress"))
                .append(" --ei progress 0 --ei phase_progress 0 --ez indeterminate true")
                .append(" --el updated_at_ms $(date +%s)000 >/dev/null 2>&1 || true; ");
        shell.append(command);
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

    private static String[] shellArguments(Context context, String runId, String command, boolean overlay) {
        String shell = resolveShell();
        String script = buildShellCommand(context, runId, command, overlay);
        if (shell.endsWith("/zsh")) return new String[]{"-lc", script};
        return new String[]{"-c", script};
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
