package io.github.mikeceead.devtoolhud;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;

public final class TaskerPluginReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !TaskerProtocol.ACTION_FIRE_SETTING.equals(intent.getAction())) return;
        intent.setExtrasClassLoader(TaskerPluginReceiver.class.getClassLoader());
        Bundle config;
        try {
            config = intent.getBundleExtra(TaskerProtocol.EXTRA_BUNDLE);
        } catch (RuntimeException exception) {
            failImmediately("Tasker action bundle could not be read. Open and save it again in Tasker.");
            return;
        }
        if (config == null || !TaskerAuthorization.matches(context,
                config.getString(TaskerProtocol.KEY_AUTH_TOKEN, ""))) {
            failImmediately("Tasker action is not authorized. Open and save it again in Tasker.");
            return;
        }
        String command = config.getString(TaskerProtocol.KEY_COMMAND, "");
        String workdir = config.getString(TaskerProtocol.KEY_WORKDIR, "~/");
        boolean background = config.getBoolean(TaskerProtocol.KEY_BACKGROUND, true);
        boolean overlay = config.getBoolean(TaskerProtocol.KEY_OVERLAY, false);
        boolean rootBridge = config.getBoolean(TaskerProtocol.KEY_ROOT_BRIDGE, false);
        TermuxCommandRunner.LaunchResult launch = rootBridge
                ? RootBridgeManager.run(context, command, workdir, overlay, intent)
                : TermuxCommandRunner.run(context, command, workdir, background, overlay, intent);
        if (launch.started) {
            if (isOrderedBroadcast()) setResultCode(TaskerProtocol.RESULT_PENDING);
        } else {
            failImmediately(launch.error);
        }
    }

    private void failImmediately(String error) {
        if (!isOrderedBroadcast()) return;
        Bundle vars = TaskerProtocol.variables(-1, -1, "", "", error);
        setResultCode(TaskerProtocol.RESULT_FAILED);
        TaskerProtocol.addVariablesToResult(getResultExtras(true), vars);
    }
}
