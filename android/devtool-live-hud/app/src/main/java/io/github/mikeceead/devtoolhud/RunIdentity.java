package io.github.mikeceead.devtoolhud;

/** Stable identifiers shared by notifications, actions, and UI-state tests. */
final class RunIdentity {
    private RunIdentity() {}

    static int notificationId(String runId) {
        String safe = runId == null ? "default" : runId;
        return 0x10000000 | (safe.hashCode() & 0x0fffffff);
    }
}
