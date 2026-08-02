package io.github.mikeceead.devtoolhud;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public final class BridgeProtocolTest {
    @Test
    public void debugBuildUsesDebugActionNamespace() {
        String packageName = "io.github.mikeceead.devtoolhud.debug";
        assertEquals(packageName + ".UPDATE", BridgeProtocol.action(packageName, "update"));
        assertTrue(BridgeProtocol.isExpectedAction(packageName, packageName + ".UPDATE"));
        assertFalse(BridgeProtocol.isExpectedAction(
                packageName, "io.github.mikeceead.devtoolhud.UPDATE"));
    }

    @Test
    public void releaseBuildUsesReleaseActionNamespace() {
        String packageName = "io.github.mikeceead.devtoolhud";
        assertEquals(packageName + ".COMPLETE", BridgeProtocol.action(packageName, "complete"));
        assertTrue(BridgeProtocol.isExpectedAction(packageName, packageName + ".TOAST"));
    }

    @Test
    public void protocolResultCodesStayStable() {
        assertEquals(1, BridgeProtocol.VERSION);
        assertEquals(100, BridgeProtocol.RESULT_ACCEPTED);
        assertEquals(110, BridgeProtocol.RESULT_IGNORED_STALE);
        assertEquals(202, BridgeProtocol.RESULT_ACTION_REJECTED);
        assertEquals(204, BridgeProtocol.RESULT_PROTOCOL_REJECTED);
    }
}
