package io.github.mikeceead.devtoolhud;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class ReleaseModelTest {
    @Test
    public void commandBuilderProducesValidatedWorkflow() {
        DevtoolRunConfig config = new DevtoolRunConfig();
        config.repo = "$HOME/Code/demo";
        config.target = "desktop";
        config.operation = "test";
        config.testProfile = "quick";
        config.testSelectors = "parser::round_trip";
        config.instrumented = false;
        String command = DevtoolCommandBuilder.build(config);
        assertTrue(command.contains("devtool --copy --copy-via termux --auto-hud"));
        assertTrue(command.contains("-r $HOME/Code/demo"));
        assertTrue(command.contains("test --profile quick --select parser::round_trip"));
    }

    @Test
    public void invalidInstrumentationCombinationIsRejected() {
        DevtoolRunConfig config = new DevtoolRunConfig();
        config.operation = "build";
        config.instrumented = true;
        assertThrows(IllegalArgumentException.class, () -> DevtoolCommandBuilder.build(config));
    }

    @Test
    public void copiedCommandRoundTripsThroughImporter() {
        DevtoolRunConfig config = new DevtoolRunConfig();
        config.operation = "status";
        config.repo = "$HOME/Code/demo repo";
        config.target = "mobile";
        String command = DevtoolCommandBuilder.build(config);
        DevtoolCommandImporter.ImportResult result = DevtoolCommandImporter.parse(command);
        assertEquals("status", result.config.operation);
        assertEquals("$HOME/Code/demo repo", result.config.repo);
        assertEquals("mobile", result.config.target);
    }

    @Test
    public void termuxPathsResolveShellHomeForms() {
        assertEquals(TermuxPaths.HOME + "/Code/demo", TermuxPaths.normalizeWorkdir("$HOME/Code/demo"));
        assertEquals(TermuxPaths.HOME + "/Code/demo", TermuxPaths.normalizeWorkdir("~/Code/demo"));
        assertEquals("/data/local/tmp", TermuxPaths.normalizeWorkdir("/data/local/tmp"));
    }

    @Test
    public void runNotificationIdentityIsStableAndDistinct() {
        assertEquals(RunIdentity.notificationId("run-a"), RunIdentity.notificationId("run-a"));
        assertNotEquals(RunIdentity.notificationId("run-a"), RunIdentity.notificationId("run-b"));
    }
}
