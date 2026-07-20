package io.github.mikeceead.devtoolhud;

import android.content.Context;

/** User-controlled retention policy for Android-indexed data only. */
final class RetentionPolicy {
    final int completedRunDays;
    final int maximumCompletedRuns;
    final int repositoryOperationDays;
    final int repositoryEventDays;

    RetentionPolicy(int completedRunDays, int maximumCompletedRuns,
                    int repositoryOperationDays, int repositoryEventDays) {
        this.completedRunDays = clamp(completedRunDays, 1, 3650);
        this.maximumCompletedRuns = clamp(maximumCompletedRuns, 25, 10000);
        this.repositoryOperationDays = clamp(repositoryOperationDays, 1, 3650);
        this.repositoryEventDays = clamp(repositoryEventDays, 1, 3650);
    }

    static RetentionPolicy from(Context context) {
        return new RetentionPolicy(
                AppSettings.completedRunRetentionDays(context),
                AppSettings.maximumCompletedRuns(context),
                AppSettings.repositoryOperationRetentionDays(context),
                AppSettings.repositoryEventRetentionDays(context));
    }

    private static int clamp(int value, int minimum, int maximum) {
        return Math.max(minimum, Math.min(maximum, value));
    }
}
