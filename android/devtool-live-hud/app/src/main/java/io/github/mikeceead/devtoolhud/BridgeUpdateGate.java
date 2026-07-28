package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

import java.util.Locale;

/** Reject delayed or replayed bridge broadcasts before they touch the UI. */
final class BridgeUpdateGate {
    private static final String PREFS = "bridge_update_order";
    private static final String KEY_ACTIVE_RUN = "active_run";
    private static final String KEY_ACTIVE_UPDATED = "active_updated_ms";

    private BridgeUpdateGate() {}

    static synchronized boolean accept(Context context, Intent intent) {
        String runId = clean(intent.getStringExtra("run_id"), "default");
        long sequence = intent.getLongExtra("sequence", 0L);
        long updatedAt = intent.getLongExtra("updated_at_ms", System.currentTimeMillis());
        String status = clean(intent.getStringExtra("status"), "running").toLowerCase(Locale.ROOT);
        boolean terminal = status.equals("ok") || status.equals("failed")
                || status.equals("cancelled") || status.equals("interrupted")
                || status.equals("skipped");

        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        long lastSequence = prefs.getLong("seq:" + runId, -1L);
        long lastUpdated = prefs.getLong("updated:" + runId, -1L);
        if (sequence > 0L && sequence < lastSequence) return false;
        // Heartbeats intentionally reuse the authoritative build sequence while
        // carrying a newer timestamp.  Accept them so custom notification
        // RemoteViews repaint while the underlying build parser is quiet.
        if (sequence > 0L && sequence == lastSequence && updatedAt <= lastUpdated) return false;
        if (sequence <= 0L && updatedAt <= lastUpdated) return false;

        String activeRun = prefs.getString(KEY_ACTIVE_RUN, "");
        long activeUpdated = prefs.getLong(KEY_ACTIVE_UPDATED, -1L);
        // A delayed broadcast from a previous run must not replace the current
        // notification. Newer timestamps can establish a new authoritative run.
        if (!activeRun.isEmpty() && !activeRun.equals(runId) && updatedAt < activeUpdated) {
            return false;
        }

        SharedPreferences.Editor editor = prefs.edit()
                .putLong("seq:" + runId, Math.max(sequence, lastSequence))
                .putLong("updated:" + runId, Math.max(updatedAt, lastUpdated));
        if (!terminal || activeRun.isEmpty() || activeRun.equals(runId) || updatedAt >= activeUpdated) {
            editor.putString(KEY_ACTIVE_RUN, runId)
                    .putLong(KEY_ACTIVE_UPDATED, Math.max(updatedAt, activeUpdated));
        }
        editor.apply();
        return true;
    }

    private static String clean(String value, String fallback) {
        if (value == null) return fallback;
        String trimmed = value.trim();
        return trimmed.isEmpty() ? fallback : trimmed;
    }
}
