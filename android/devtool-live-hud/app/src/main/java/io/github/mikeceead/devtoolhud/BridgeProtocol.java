package io.github.mikeceead.devtoolhud;

import java.util.Locale;

/** Shared terminal-to-app bridge protocol constants and action validation. */
final class BridgeProtocol {
    static final int VERSION = 1;

    static final int RESULT_ACCEPTED = 100;
    static final int RESULT_IGNORED_STALE = 110;
    static final int RESULT_TOKEN_REJECTED = 201;
    static final int RESULT_ACTION_REJECTED = 202;
    static final int RESULT_RUN_ID_MISSING = 203;
    static final int RESULT_PROTOCOL_REJECTED = 204;

    private static final String[] ACTIONS = new String[]{
            "UPDATE", "COMPLETE", "FAIL", "CANCEL", "TOAST"
    };

    private BridgeProtocol() {}

    static String action(String packageName, String actionName) {
        String cleanPackage = packageName == null ? "" : packageName.trim();
        String cleanAction = actionName == null ? "" : actionName.trim().toUpperCase(Locale.ROOT);
        return cleanPackage + "." + cleanAction;
    }

    static boolean isExpectedAction(String packageName, String action) {
        if (action == null || action.isEmpty()) return false;
        for (String candidate : ACTIONS) {
            if (action(packageName, candidate).equals(action)) return true;
        }
        return false;
    }
}
