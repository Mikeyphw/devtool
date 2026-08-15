package io.github.mikeceead.devtoolhud;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public final class SupportRedactorTest {
    @Test
    public void redactsCredentialsPrivateKeysAndQuotedValues() {
        String input = "token=abc password:'secret value' Authorization: Bearer deadbeef "
                + "https://user:pass@example.com/repo.git bridge-token "
                + "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----";
        String output = SupportRedactor.redact(input, "bridge-token");
        assertFalse(output.contains("secret value"));
        assertFalse(output.contains("deadbeef"));
        assertFalse(output.contains("user:pass"));
        assertFalse(output.contains("OPENSSH PRIVATE KEY"));
        assertTrue(output.contains("<redacted:bridge-token>"));
        assertTrue(output.contains("https://<redacted>@example.com/repo.git"));
    }

    @Test
    public void denseNearMatchesRemainLinearAndOversizedRecordsFailClosed() {
        String dense = "=".repeat(200_000) + "\nPASSWORD=secret\n";
        long started = System.nanoTime();
        String output = SupportRedactor.redact(dense);
        long elapsedMillis = (System.nanoTime() - started) / 1_000_000L;
        assertTrue("redaction unexpectedly slow: " + elapsedMillis + "ms", elapsedMillis < 1_500L);
        assertFalse(output.contains("secret"));

        String oversized = "TOKEN=\"" + "s".repeat((2 * 1024 * 1024) + 64);
        String redacted = SupportRedactor.redact(oversized);
        assertTrue(redacted.contains("oversized-log-record-redacted"));
        assertFalse(redacted.contains("ssssssss"));
    }
}
