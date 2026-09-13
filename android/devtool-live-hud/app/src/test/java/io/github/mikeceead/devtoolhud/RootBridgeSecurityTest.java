package io.github.mikeceead.devtoolhud;

import org.junit.Test;

import java.lang.reflect.Method;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public final class RootBridgeSecurityTest {
    private static boolean allowed(String command) throws Exception {
        Method method = RootBridgeManager.class.getDeclaredMethod("isAllowedDevtoolCommand", String.class);
        method.setAccessible(true);
        return (Boolean) method.invoke(null, command);
    }

    @Test
    public void generatedDevtoolCommandRemainsAllowed() throws Exception {
        assertTrue(allowed("CI=1 devtool validate --phase unit-test"));
        assertTrue(allowed("devtool-androidos build"));
    }

    @Test
    public void doubleQuotedDollarCommandSubstitutionIsRejected() throws Exception {
        assertFalse(allowed("devtool validate --phase \"$(id)\""));
        assertFalse(allowed("EVIL=\"$(id)\" devtool validate"));
    }

    @Test
    public void doubleQuotedBacktickCommandSubstitutionIsRejected() throws Exception {
        assertFalse(allowed("devtool validate --phase \"`id`\""));
    }
}
