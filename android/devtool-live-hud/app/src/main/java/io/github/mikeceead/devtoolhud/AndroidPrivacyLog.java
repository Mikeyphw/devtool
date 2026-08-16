package io.github.mikeceead.devtoolhud;

import android.util.Log;

/**
 * Release-safe Logcat shim for the Android HUD.
 *
 * Android's own security guidance treats Logcat as a privacy boundary: app
 * logs can leak command text, paths, credentials, and bridge material.  Keep
 * production builds silent, and route debug-build messages through the same
 * bounded support-bundle redactor used by exported diagnostics.
 */
final class AndroidPrivacyLog {
    private AndroidPrivacyLog() {}

    static void debug(String tag, String message) {
        if (BuildConfig.DEBUG) Log.d(cleanTag(tag), clean(message));
    }

    static void info(String tag, String message) {
        if (BuildConfig.DEBUG) Log.i(cleanTag(tag), clean(message));
    }

    static void warn(String tag, String message) {
        if (BuildConfig.DEBUG) Log.w(cleanTag(tag), clean(message));
    }

    static void warn(String tag, String message, Throwable error) {
        if (BuildConfig.DEBUG) Log.w(cleanTag(tag), clean(message) + throwableSummary(error));
    }

    static void error(String tag, String message) {
        if (BuildConfig.DEBUG) Log.e(cleanTag(tag), clean(message));
    }

    static void error(String tag, String message, Throwable error) {
        if (BuildConfig.DEBUG) Log.e(cleanTag(tag), clean(message) + throwableSummary(error));
    }

    private static String clean(String value) {
        String redacted = SupportRedactor.redact(value == null ? "" : value);
        return redacted.length() <= 1200 ? redacted : redacted.substring(0, 1200) + "…";
    }

    private static String throwableSummary(Throwable error) {
        if (error == null) return "";
        String detail = error.getMessage();
        String suffix = detail == null || detail.trim().isEmpty()
                ? error.getClass().getSimpleName()
                : error.getClass().getSimpleName() + ": " + detail;
        return " · " + clean(suffix);
    }

    private static String cleanTag(String tag) {
        String clean = tag == null || tag.trim().isEmpty() ? "DevtoolHUD" : tag.trim();
        return clean.length() <= 23 ? clean : clean.substring(0, 23);
    }
}
