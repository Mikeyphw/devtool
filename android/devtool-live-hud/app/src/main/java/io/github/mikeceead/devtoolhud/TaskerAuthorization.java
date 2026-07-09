package io.github.mikeceead.devtoolhud;

import android.content.ComponentName;
import android.content.Context;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;

final class TaskerAuthorization {
    private static final String PREFS = "tasker_authorization";
    private static final String KEY_TOKEN = "token";
    private static final String TASKER = "net.dinglisch.android.taskerm";
    private static final String TASKER_LEGACY = "net.dinglisch.android.tasker";

    private TaskerAuthorization() {}

    static boolean isTrustedEditor(ComponentName caller) {
        if (caller == null) return false;
        String packageName = caller.getPackageName();
        return TASKER.equals(packageName) || TASKER_LEGACY.equals(packageName);
    }

    static String token(Context context) {
        String existing = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getString(KEY_TOKEN, "");
        if (existing != null && !existing.isEmpty()) return existing;
        byte[] bytes = new byte[32];
        new SecureRandom().nextBytes(bytes);
        String created = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().putString(KEY_TOKEN, created).apply();
        return created;
    }

    static boolean matches(Context context, String supplied) {
        if (supplied == null || supplied.isEmpty()) return false;
        return MessageDigest.isEqual(
                token(context).getBytes(StandardCharsets.UTF_8),
                supplied.getBytes(StandardCharsets.UTF_8));
    }
}
