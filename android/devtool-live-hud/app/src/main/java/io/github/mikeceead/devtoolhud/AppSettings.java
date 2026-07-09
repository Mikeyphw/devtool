package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.SharedPreferences;

final class AppSettings {
    private static final String PREFS = "app_settings";
    private static final String KEY_MONET = "monet";
    private static final String KEY_COMMAND = "command";
    private static final String KEY_WORKDIR = "workdir";
    private static final String KEY_BACKGROUND = "background";
    private static final String KEY_OVERLAY = "overlay_default";
    private static final String KEY_PAGE = "page";
    private static final String KEY_OPERATION = "operation";
    private static final String KEY_REPO = "repo";
    private static final String KEY_RUN_WORKDIR = "run_workdir";
    private static final String KEY_TARGET = "target";
    private static final String KEY_ARTIFACT = "artifact";
    private static final String KEY_COMMIT_MESSAGE = "commit_message";
    private static final String KEY_CUSTOM_ARGUMENTS = "custom_arguments";
    private static final String KEY_ENVIRONMENT = "environment_assignments";
    private static final String KEY_EXTRA_GLOBAL = "extra_global_arguments";
    private static final String KEY_EXTRA_OPERATION = "extra_operation_arguments";
    private static final String KEY_CHROOT = "chroot";
    private static final String KEY_USE_CHROOT = "use_chroot";
    private static final String KEY_USE_ROOT_BRIDGE = "use_root_bridge";
    private static final String KEY_COPY = "copy";
    private static final String KEY_COPY_VIA_TERMUX = "copy_via_termux";
    private static final String KEY_AUTO_HUD = "auto_hud";
    private static final String KEY_VALIDATE = "validate";
    private static final String KEY_COMMIT = "commit";
    private static final String KEY_YES = "assume_yes";

    private AppSettings() {}

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static boolean useMonet(Context context) { return prefs(context).getBoolean(KEY_MONET, false); }
    static void setUseMonet(Context context, boolean value) { prefs(context).edit().putBoolean(KEY_MONET, value).apply(); }

    static String command(Context context) {
        return prefs(context).getString(KEY_COMMAND,
                "devtool --copy --auto-hud --hud-mode android-bridge --yes --help");
    }
    static void setCommand(Context context, String value) { prefs(context).edit().putString(KEY_COMMAND, clean(value)).apply(); }

    static String workdir(Context context) { return prefs(context).getString(KEY_WORKDIR, "~/"); }
    static void setWorkdir(Context context, String value) {
        String clean = clean(value);
        prefs(context).edit().putString(KEY_WORKDIR, clean.isEmpty() ? "~/" : clean).apply();
    }

    static boolean runInBackground(Context context) { return prefs(context).getBoolean(KEY_BACKGROUND, true); }
    static void setRunInBackground(Context context, boolean value) { prefs(context).edit().putBoolean(KEY_BACKGROUND, value).apply(); }

    static boolean overlayByDefault(Context context) { return prefs(context).getBoolean(KEY_OVERLAY, true); }
    static void setOverlayByDefault(Context context, boolean value) { prefs(context).edit().putBoolean(KEY_OVERLAY, value).apply(); }

    static int page(Context context) { return prefs(context).getInt(KEY_PAGE, 0); }
    static void setPage(Context context, int value) { prefs(context).edit().putInt(KEY_PAGE, value).apply(); }

    static DevtoolRunConfig runConfig(Context context) {
        SharedPreferences p = prefs(context);
        DevtoolRunConfig config = new DevtoolRunConfig();
        config.operation = p.getString(KEY_OPERATION, config.operation);
        config.repo = p.getString(KEY_REPO, config.repo);
        config.workdir = p.getString(KEY_RUN_WORKDIR, config.workdir);
        config.target = p.getString(KEY_TARGET, config.target);
        config.artifact = p.getString(KEY_ARTIFACT, config.artifact);
        config.commitMessage = p.getString(KEY_COMMIT_MESSAGE, config.commitMessage);
        config.customArguments = p.getString(KEY_CUSTOM_ARGUMENTS, config.customArguments);
        config.environmentAssignments = p.getString(KEY_ENVIRONMENT, config.environmentAssignments);
        config.extraGlobalArguments = p.getString(KEY_EXTRA_GLOBAL, config.extraGlobalArguments);
        config.extraOperationArguments = p.getString(KEY_EXTRA_OPERATION, config.extraOperationArguments);
        config.chrootName = p.getString(KEY_CHROOT, config.chrootName);
        config.useChroot = p.getBoolean(KEY_USE_CHROOT, config.useChroot);
        config.useRootBridge = p.getBoolean(KEY_USE_ROOT_BRIDGE, config.useRootBridge);
        config.copy = p.getBoolean(KEY_COPY, config.copy);
        config.copyViaTermux = p.getBoolean(KEY_COPY_VIA_TERMUX, config.copyViaTermux);
        config.autoHud = p.getBoolean(KEY_AUTO_HUD, config.autoHud);
        config.validate = p.getBoolean(KEY_VALIDATE, config.validate);
        config.commit = p.getBoolean(KEY_COMMIT, config.commit);
        config.assumeYes = p.getBoolean(KEY_YES, config.assumeYes);
        config.overlay = p.getBoolean(KEY_OVERLAY, config.overlay);
        return config;
    }

    static void setRunConfig(Context context, DevtoolRunConfig config) {
        prefs(context).edit()
                .putString(KEY_OPERATION, clean(config.operation))
                .putString(KEY_REPO, clean(config.repo))
                .putString(KEY_RUN_WORKDIR, clean(config.workdir))
                .putString(KEY_TARGET, clean(config.target))
                .putString(KEY_ARTIFACT, clean(config.artifact))
                .putString(KEY_COMMIT_MESSAGE, clean(config.commitMessage))
                .putString(KEY_CUSTOM_ARGUMENTS, clean(config.customArguments))
                .putString(KEY_ENVIRONMENT, clean(config.environmentAssignments))
                .putString(KEY_EXTRA_GLOBAL, clean(config.extraGlobalArguments))
                .putString(KEY_EXTRA_OPERATION, clean(config.extraOperationArguments))
                .putString(KEY_CHROOT, clean(config.chrootName))
                .putBoolean(KEY_USE_CHROOT, config.useChroot)
                .putBoolean(KEY_USE_ROOT_BRIDGE, config.useRootBridge)
                .putBoolean(KEY_COPY, config.copy)
                .putBoolean(KEY_COPY_VIA_TERMUX, config.copyViaTermux)
                .putBoolean(KEY_AUTO_HUD, config.autoHud)
                .putBoolean(KEY_VALIDATE, config.validate)
                .putBoolean(KEY_COMMIT, config.commit)
                .putBoolean(KEY_YES, config.assumeYes)
                .putBoolean(KEY_OVERLAY, config.overlay)
                .apply();
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
