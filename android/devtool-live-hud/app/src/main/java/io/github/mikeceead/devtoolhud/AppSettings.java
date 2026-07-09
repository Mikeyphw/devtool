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

    private AppSettings() {}

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static boolean useMonet(Context context) {
        return prefs(context).getBoolean(KEY_MONET, false);
    }

    static void setUseMonet(Context context, boolean value) {
        prefs(context).edit().putBoolean(KEY_MONET, value).apply();
    }

    static String command(Context context) {
        return prefs(context).getString(KEY_COMMAND,
                "devtool --copy --auto-hud --hud-mode android-bridge --yes --help");
    }

    static void setCommand(Context context, String value) {
        prefs(context).edit().putString(KEY_COMMAND, clean(value)).apply();
    }

    static String workdir(Context context) {
        return prefs(context).getString(KEY_WORKDIR, "~/");
    }

    static void setWorkdir(Context context, String value) {
        String clean = clean(value);
        prefs(context).edit().putString(KEY_WORKDIR, clean.isEmpty() ? "~/" : clean).apply();
    }

    static boolean runInBackground(Context context) {
        return prefs(context).getBoolean(KEY_BACKGROUND, true);
    }

    static void setRunInBackground(Context context, boolean value) {
        prefs(context).edit().putBoolean(KEY_BACKGROUND, value).apply();
    }

    static boolean overlayByDefault(Context context) {
        return prefs(context).getBoolean(KEY_OVERLAY, false);
    }

    static void setOverlayByDefault(Context context, boolean value) {
        prefs(context).edit().putBoolean(KEY_OVERLAY, value).apply();
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
