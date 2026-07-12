package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

final class BridgeTokenManager {
    static final String ACTION_UPDATED = "io.github.mikeceead.devtoolhud.BRIDGE_TOKEN_UPDATED";
    static final String OP_INSTALL = "install";
    static final String OP_VERIFY = "verify";

    private static final String PREFS = "bridge_token_state";
    private static final String KEY_VERIFIED_HASH = "verified_hash";
    private static final String KEY_BUSY = "busy";
    private static final String KEY_ERROR = "error";

    private BridgeTokenManager() {}

    static String state(Context context) {
        if (!TermuxCommandRunner.isInstalled(context)) return "termux-missing";
        if (!TermuxCommandRunner.hasPermission(context)) return "permission-needed";
        SharedPreferences prefs = prefs(context);
        if (prefs.getBoolean(KEY_BUSY, false)) return "working";
        String verified = prefs.getString(KEY_VERIFIED_HASH, "");
        if (verified == null || verified.isEmpty()) return "not-installed";
        return verified.equals(hash(BridgeSecrets.getOrCreateToken(context))) ? "connected" : "outdated";
    }

    static String detail(Context context) {
        String error = prefs(context).getString(KEY_ERROR, "");
        if (error != null && !error.trim().isEmpty()) return error.trim();
        switch (state(context)) {
            case "connected": return "The app and Termux use the same verified bridge token.";
            case "outdated": return "The app token changed. Update the Termux copy.";
            case "working": return "Termux is checking the secure bridge token.";
            case "permission-needed": return "Grant Termux command access before installing the token.";
            case "termux-missing": return "Install Termux before pairing the live HUD bridge.";
            default: return "Install the token directly into Termux with one tap.";
        }
    }

    static String actionLabel(Context context) {
        switch (state(context)) {
            case "connected": return "Verify";
            case "outdated": return "Update token";
            case "working": return "Working…";
            case "permission-needed": return "Grant access";
            case "termux-missing": return "Termux unavailable";
            default: return "Install token";
        }
    }

    static boolean isReady(Context context) {
        return "connected".equals(state(context));
    }

    static TermuxCommandRunner.LaunchResult performPrimaryAction(Context context) {
        String state = state(context);
        if (state.equals("connected")) return verify(context);
        if (state.equals("working")) return new TermuxCommandRunner.LaunchResult(false, -1, "Token operation is already running");
        return install(context);
    }

    static TermuxCommandRunner.LaunchResult install(Context context) {
        String token = BridgeSecrets.getOrCreateToken(context);
        String expectedHash = hash(token);
        String quoted = shellQuote(token);
        String command = "set -eu; "
                + "dir=\"$HOME/.config/devtool\"; file=\"$dir/android-live-hud.token\"; "
                + "mkdir -p \"$dir\"; umask 077; tmp=\"$file.tmp.$$\"; "
                + "printf '%s\\n' " + quoted + " > \"$tmp\"; chmod 600 \"$tmp\"; mv -f \"$tmp\" \"$file\"; "
                + "test \"$(cat \"$file\")\" = " + quoted + "; printf 'DEVTOOL_BRIDGE_TOKEN_OK\\n'";
        return launch(context, OP_INSTALL, expectedHash, command);
    }

    static TermuxCommandRunner.LaunchResult verify(Context context) {
        String token = BridgeSecrets.getOrCreateToken(context);
        String expectedHash = hash(token);
        String quoted = shellQuote(token);
        String command = "set -eu; file=\"$HOME/.config/devtool/android-live-hud.token\"; "
                + "test -f \"$file\"; test \"$(cat \"$file\")\" = " + quoted
                + "; test \"$(stat -c '%a' \"$file\" 2>/dev/null || stat -f '%Lp' \"$file\")\" = 600; "
                + "printf 'DEVTOOL_BRIDGE_TOKEN_OK\\n'";
        return launch(context, OP_VERIFY, expectedHash, command);
    }

    private static TermuxCommandRunner.LaunchResult launch(Context context, String operation,
                                                            String expectedHash, String command) {
        prefs(context).edit().putBoolean(KEY_BUSY, true).putString(KEY_ERROR, "").apply();
        notifyChanged(context);
        TermuxCommandRunner.LaunchResult result = TermuxCommandRunner.runBridgeOperation(
                context, command, operation, expectedHash);
        if (!result.started) {
            prefs(context).edit().putBoolean(KEY_BUSY, false).putString(KEY_ERROR, result.error).apply();
            notifyChanged(context);
        }
        return result;
    }

    static void handleResult(Context context, String operation, String expectedHash,
                             int exitCode, String stdout, String stderr, String error) {
        boolean marker = stdout != null && stdout.contains("DEVTOOL_BRIDGE_TOKEN_OK");
        boolean ok = exitCode == 0 && (error == null || error.trim().isEmpty()) && marker;
        SharedPreferences.Editor edit = prefs(context).edit().putBoolean(KEY_BUSY, false);
        if (ok) {
            edit.putString(KEY_VERIFIED_HASH, expectedHash).putString(KEY_ERROR, "");
        } else {
            String detail = firstNonEmpty(error, stderr,
                    operation.equals(OP_VERIFY) ? "Token verification failed" : "Token installation failed");
            edit.putString(KEY_ERROR, detail);
            if (operation.equals(OP_VERIFY)) edit.remove(KEY_VERIFIED_HASH);
        }
        edit.apply();
        notifyChanged(context);
    }

    static void invalidate(Context context) {
        prefs(context).edit().putBoolean(KEY_BUSY, false).putString(KEY_ERROR, "").apply();
        notifyChanged(context);
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static void notifyChanged(Context context) {
        context.sendBroadcast(new Intent(ACTION_UPDATED).setPackage(context.getPackageName()));
    }

    private static String hash(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder output = new StringBuilder(digest.length * 2);
            for (byte item : digest) output.append(String.format("%02x", item & 0xff));
            return output.toString();
        } catch (Exception ignored) {
            return Integer.toHexString(value.hashCode());
        }
    }

    private static String shellQuote(String value) {
        return "'" + value.replace("'", "'\\''") + "'";
    }

    private static String firstNonEmpty(String... values) {
        for (String value : values) {
            String clean = HudUpdate.plainText(value);
            if (!clean.isEmpty()) return clean;
        }
        return "Unknown bridge error";
    }
}
