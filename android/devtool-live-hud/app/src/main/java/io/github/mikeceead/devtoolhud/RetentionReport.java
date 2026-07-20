package io.github.mikeceead.devtoolhud;

/** Result of one indexed-data cleanup pass. */
final class RetentionReport {
    int runsRemoved;
    int logEntriesRemoved;
    int operationsRemoved;
    int eventsRemoved;
    long databaseBytesBefore;
    long databaseBytesAfter;
    String error = "";

    String summary() {
        if (!error.isEmpty()) return "Retention failed: " + error;
        return runsRemoved + " runs · " + logEntriesRemoved + " log entries · "
                + operationsRemoved + " Git operations · " + eventsRemoved + " activity events removed";
    }
}
