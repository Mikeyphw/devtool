package io.github.mikeceead.devtoolhud;

import android.content.Context;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Runs bounded cleanup without blocking Activity startup or the main thread. */
final class RetentionManager {
    interface Callback { void complete(RetentionReport report); }

    private static final ExecutorService EXECUTOR = Executors.newSingleThreadExecutor();
    private static final long AUTOMATIC_INTERVAL_MS = 24L * 60L * 60L * 1000L;

    private RetentionManager() {}

    static void maybeRun(Context context) {
        Context app = context.getApplicationContext();
        long now = System.currentTimeMillis();
        if (now - AppSettings.lastRetentionAt(app) < AUTOMATIC_INTERVAL_MS) return;
        EXECUTOR.execute(() -> {
            try {
                DevtoolDatabase.get(app).applyRetention(RetentionPolicy.from(app), now, false);
                AppSettings.setLastRetentionAt(app, now);
            } catch (RuntimeException ignored) {
                // Retry on the next eligible startup. Cleanup must never block app launch.
            }
        });
    }

    static void runNow(Context context, boolean compactDatabase, Callback callback) {
        Context app = context.getApplicationContext();
        EXECUTOR.execute(() -> {
            long now = System.currentTimeMillis();
            RetentionReport report;
            try {
                report = DevtoolDatabase.get(app)
                        .applyRetention(RetentionPolicy.from(app), now, compactDatabase);
                AppSettings.setLastRetentionAt(app, now);
            } catch (RuntimeException failure) {
                report = new RetentionReport();
                report.error = failure.getMessage() == null
                        ? failure.getClass().getSimpleName() : failure.getMessage();
            }
            if (callback != null) callback.complete(report);
        });
    }
}
