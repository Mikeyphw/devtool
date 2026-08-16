package io.github.mikeceead.devtoolhud;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.util.Log;

import java.net.URISyntaxException;

final class TaskerProtocol {
    static final String ACTION_EDIT_SETTING = "com.twofortyfouram.locale.intent.action.EDIT_SETTING";
    static final String ACTION_FIRE_SETTING = "com.twofortyfouram.locale.intent.action.FIRE_SETTING";
    static final String EXTRA_BUNDLE = "com.twofortyfouram.locale.intent.extra.BUNDLE";
    static final String EXTRA_BLURB = "com.twofortyfouram.locale.intent.extra.BLURB";

    static final String KEY_COMMAND = "command";
    static final String KEY_WORKDIR = "workdir";
    static final String KEY_BACKGROUND = "background";
    static final String KEY_OVERLAY = "overlay";
    static final String KEY_ROOT_BRIDGE = "root_bridge";
    static final String KEY_AUTH_TOKEN = "authorization_token";

    static final int RESULT_OK = Activity.RESULT_OK;
    static final int RESULT_FAILED = Activity.RESULT_FIRST_USER + 1;
    static final int RESULT_PENDING = Activity.RESULT_FIRST_USER + 2;

    private static final String TAG = "DevtoolTasker";
    private static final String BASE = "net.dinglisch.android.tasker";
    private static final String EXTRAS = BASE + ".extras.";
    private static final String EXTRA_VARIABLES = EXTRAS + "VARIABLES";
    private static final String EXTRA_RESULT_CODE = EXTRAS + "RESULT_CODE";
    private static final String EXTRA_COMPLETION_INTENT = EXTRAS + "COMPLETION_INTENT";
    private static final String EXTRA_REQUESTED_TIMEOUT = EXTRAS + "REQUESTED_TIMEOUT";
    private static final String EXTRA_VARIABLE_REPLACE_KEYS = EXTRAS + "VARIABLE_REPLACE_KEYS";
    private static final String EXTRA_RELEVANT_VARIABLES = BASE + ".RELEVANT_VARIABLES";
    private static final String EXTRA_CALL_SERVICE_PACKAGE = "net.dinglisch.android.tasker.EXTRA_CALL_SERVICE_PACKAGE";
    private static final String EXTRA_CALL_SERVICE = "net.dinglisch.android.tasker.EXTRA_CALL_SERVICE";
    private static final String EXTRA_CALL_SERVICE_FOREGROUND = "net.dinglisch.android.tasker.EXTRA_CALL_SERVICE_FOREGROUND";

    private TaskerProtocol() {}

    static void decorateEditResult(Intent result, Bundle config) {
        result.putExtra(EXTRA_BUNDLE, config);
        result.putExtra(EXTRA_BLURB, blurb(config));
        result.putExtra(EXTRA_REQUESTED_TIMEOUT, 3_599_000);
        result.putExtra(EXTRA_RELEVANT_VARIABLES, new String[]{
                "%devtool_execution_id\nExecution ID\nUnique identifier for this devtool command.",
                "%devtool_exit_code\nExit code\nShell exit code returned by the selected backend.",
                "%devtool_stdout\nStandard output\nCaptured stdout, or the foreground session transcript.",
                "%devtool_stderr\nStandard error\nCaptured stderr for background commands.",
                "%devtool_error\nExecution error\nBackend or plug-in error details.",
                "%errmsg\nError message\nHuman-readable Tasker failure message."
        });
        config.putString(EXTRA_VARIABLE_REPLACE_KEYS, KEY_COMMAND + " " + KEY_WORKDIR);
    }

    static String blurb(Bundle config) {
        String command = config.getString(KEY_COMMAND, "devtool").trim();
        String shortCommand = command.length() > 54 ? command.substring(0, 51) + "…" : command;
        return (config.getBoolean(KEY_ROOT_BRIDGE, false) ? "Run with root bridge: " : "Run in Termux: ") + shortCommand;
    }

    static void addVariablesToResult(Bundle resultExtras, Bundle variables) {
        if (resultExtras != null && variables != null) resultExtras.putBundle(EXTRA_VARIABLES, variables);
    }

    static Bundle variables(int executionId, int exitCode, String stdout, String stderr, String error) {
        Bundle vars = new Bundle();
        vars.putString("%devtool_execution_id", String.valueOf(executionId));
        vars.putString("%devtool_exit_code", String.valueOf(exitCode));
        vars.putString("%devtool_stdout", safe(stdout));
        vars.putString("%devtool_stderr", safe(stderr));
        String safeError = safe(error);
        vars.putString("%devtool_error", safeError);
        if (!safeError.isEmpty()) vars.putString("%errmsg", safeError);
        return vars;
    }

    static boolean signalFinish(Context context, Intent originalFireIntent, int resultCode, Bundle variables) {
        if (originalFireIntent == null) return false;
        String encoded = originalFireIntent.getStringExtra(EXTRA_COMPLETION_INTENT);
        if (encoded == null || encoded.trim().isEmpty()) return false;
        try {
            Intent completion = Intent.parseUri(encoded, Intent.URI_INTENT_SCHEME);
            completion.putExtra(EXTRA_RESULT_CODE, resultCode);
            if (variables != null) completion.putExtra(EXTRA_VARIABLES, variables);
            String servicePackage = completion.getStringExtra(EXTRA_CALL_SERVICE_PACKAGE);
            String serviceClass = completion.getStringExtra(EXTRA_CALL_SERVICE);
            boolean foreground = completion.getBooleanExtra(EXTRA_CALL_SERVICE_FOREGROUND, false);
            if (servicePackage != null && serviceClass != null) {
                if (!TaskerAuthorization.isTrustedPackage(servicePackage)) return false;
                completion.setComponent(new ComponentName(servicePackage, serviceClass));
                if (foreground) context.startForegroundService(completion);
                else context.startService(completion);
            } else {
                ComponentName component = completion.getComponent();
                String packageName = component == null ? completion.getPackage() : component.getPackageName();
                if (packageName != null && !TaskerAuthorization.isTrustedPackage(packageName)) return false;
                if (packageName == null) completion.setPackage("net.dinglisch.android.taskerm");
                context.sendBroadcast(completion);
            }
            return true;
        } catch (URISyntaxException | RuntimeException error) {
            Log.e(TAG, "Unable to return Tasker plug-in result", error);
            return false;
        }
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }
}
