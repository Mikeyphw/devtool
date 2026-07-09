package io.github.mikeceead.devtoolhud;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;

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
    private static final SecureRandom IDS = new SecureRandom();

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
        String cleanCommand = command == null ? "" : command.trim();
        if (cleanCommand.isEmpty()) return new LaunchResult(false, -1, "Command is empty");
        if (cleanCommand.length() > 120_000) return new LaunchResult(false, -1, "Command is too long");
        if (!isInstalled(context)) return new LaunchResult(false, -1, "Termux is not installed");
        if (!hasPermission(context)) return new LaunchResult(false, -1, "Run commands in Termux permission is not granted");

        int executionId = nextExecutionId();
        Intent resultIntent = new Intent(context, TermuxResultService.class)
                .putExtra(TermuxResultService.EXTRA_EXECUTION_ID, executionId)
                .putExtra(TermuxResultService.EXTRA_TASKER_FIRE_INTENT, taskerFireIntent)
                .putExtra(TermuxResultService.EXTRA_OVERLAY, overlay);
        int flags = PendingIntent.FLAG_ONE_SHOT | PendingIntent.FLAG_CANCEL_CURRENT;
        flags |= Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0;
        PendingIntent result = PendingIntent.getService(context, executionId, resultIntent, flags);

        Intent request = new Intent()
                .setClassName(TERMUX_PACKAGE, SERVICE)
                .setAction(ACTION)
                .putExtra(EXTRA_PATH, "$PREFIX/bin/bash")
                .putExtra(EXTRA_ARGUMENTS, new String[]{"-lc",
                        overlay ? "export DEVTOOL_HUD_OVERLAY=1; " + cleanCommand : cleanCommand})
                .putExtra(EXTRA_WORKDIR, cleanWorkdir(workdir))
                .putExtra(EXTRA_BACKGROUND, background)
                .putExtra(EXTRA_PENDING_INTENT, result)
                .putExtra(EXTRA_LABEL, "Devtool Live HUD")
                .putExtra(EXTRA_DESCRIPTION, "Runs a user-configured devtool command and returns its result.");
        try {
            context.startService(request);
            HudUpdate started = HudUpdate.commandStarted(executionId, cleanCommand, cleanWorkdir(workdir), overlay);
            new NotificationController(context).post(started);
            OverlayHudService.publish(context, started);
            return new LaunchResult(true, executionId, "");
        } catch (RuntimeException error) {
            return new LaunchResult(false, executionId,
                    error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage());
        }
    }

    private static int nextExecutionId() {
        int value;
        do value = IDS.nextInt(Integer.MAX_VALUE);
        while (value == 0);
        return value;
    }

    private static String cleanWorkdir(String workdir) {
        String value = workdir == null ? "" : workdir.trim();
        return value.isEmpty() ? "~/" : value;
    }
}
