package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.SharedPreferences;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;

final class BridgeSecrets {
    private static final String PREFS = "bridge_secrets";
    private static final String KEY_TOKEN = "token";
    private static final String KEY_GENERATION = "generation";

    private BridgeSecrets() {}

    static String getOrCreateToken(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String token = prefs.getString(KEY_TOKEN, "");
        if (token != null && token.length() >= 32) {
            if (prefs.getLong(KEY_GENERATION, 0L) <= 0L) {
                prefs.edit().putLong(KEY_GENERATION, 1L).apply();
            }
            return token;
        }
        return regenerateToken(context);
    }

    static String regenerateToken(Context context) {
        byte[] bytes = new byte[24];
        new SecureRandom().nextBytes(bytes);
        StringBuilder out = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) out.append(String.format("%02x", value & 0xff));
        String token = out.toString();
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        long nextGeneration = Math.max(1L, prefs.getLong(KEY_GENERATION, 0L) + 1L);
        prefs.edit().putString(KEY_TOKEN, token).putLong(KEY_GENERATION, nextGeneration).apply();
        BridgeTokenManager.invalidate(context);
        return token;
    }

    static long generation(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        long value = prefs.getLong(KEY_GENERATION, 0L);
        if (value <= 0L) {
            getOrCreateToken(context);
            value = Math.max(1L, prefs.getLong(KEY_GENERATION, 1L));
        }
        return value;
    }

    static boolean accepts(Context context, String candidate) {
        if (candidate == null || candidate.isEmpty()) return false;
        byte[] expected = getOrCreateToken(context).getBytes(StandardCharsets.UTF_8);
        byte[] actual = candidate.getBytes(StandardCharsets.UTF_8);
        return MessageDigest.isEqual(expected, actual);
    }
}
