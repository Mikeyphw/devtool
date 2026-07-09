package io.github.mikeceead.devtoolhud;

import android.app.Activity;
import android.content.ClipboardManager;
import android.content.Intent;
import android.os.Bundle;
import android.view.View;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

public final class TaskerPluginActivity extends Activity {
    private EditText command;
    private EditText workdir;
    private Switch background;
    private Switch overlay;
    private Switch rootBridge;
    private UiKit ui;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        if (!TaskerAuthorization.isTrustedEditor(getCallingActivity())) {
            Toast.makeText(this, "This configuration screen can only be opened by Tasker.", Toast.LENGTH_LONG).show();
            setResult(RESULT_CANCELED);
            finish();
            return;
        }
        ui = new UiKit(this);
        getWindow().setStatusBarColor(ui.palette.background);
        getWindow().setNavigationBarColor(ui.palette.background);
        setContentView(buildUi());
        loadConfig();
    }

    @Override
    public void onBackPressed() {
        saveAndFinish();
    }

    private View buildUi() {
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(ui.dp(20), ui.dp(18), ui.dp(20), ui.dp(24));
        content.setBackgroundColor(ui.palette.background);
        TextView title = ui.text("Run devtool", 25, ui.palette.text, true);
        content.addView(title);
        TextView subtitle = ui.text("Choose the rootless Termux backend or the optional paired root bridge. Tasker waits for completion and receives the result.",
                14, ui.palette.muted, false);
        subtitle.setPadding(0, ui.dp(6), 0, ui.dp(12));
        content.addView(subtitle);

        LinearLayout settings = ui.surface();
        command = ui.input("", "devtool command", true);
        workdir = ui.input("", "Termux working directory", false);
        background = ui.toggle("Run in background (separate stdout/stderr)", true);
        overlay = ui.toggle("Request floating HUD", false);
        rootBridge = ui.toggle("Use optional root bridge", false);
        rootBridge.setEnabled(RootBridgeManager.isReady(this));
        settings.addView(command);
        settings.addView(ui.secondaryButton("Paste command from clipboard", v -> pasteCommand()));
        settings.addView(workdir);
        settings.addView(background);
        settings.addView(overlay);
        settings.addView(rootBridge);
        settings.addView(ui.button("Save Tasker action", ui.palette.green, v -> saveAndFinish()));
        settings.addView(ui.secondaryButton("Cancel", v -> {
            setResult(RESULT_CANCELED);
            finish();
        }));
        content.addView(settings);
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.addView(content);
        return scroll;
    }

    private void loadConfig() {
        Bundle config = getIntent().getBundleExtra(TaskerProtocol.EXTRA_BUNDLE);
        command.setText(config == null ? AppSettings.command(this)
                : config.getString(TaskerProtocol.KEY_COMMAND, AppSettings.command(this)));
        workdir.setText(config == null ? AppSettings.workdir(this)
                : config.getString(TaskerProtocol.KEY_WORKDIR, AppSettings.workdir(this)));
        background.setChecked(config == null ? AppSettings.runInBackground(this)
                : config.getBoolean(TaskerProtocol.KEY_BACKGROUND, true));
        overlay.setChecked(config == null ? AppSettings.overlayByDefault(this)
                : config.getBoolean(TaskerProtocol.KEY_OVERLAY, false));
        rootBridge.setChecked(RootBridgeManager.isReady(this) && config != null
                && config.getBoolean(TaskerProtocol.KEY_ROOT_BRIDGE, false));
    }

    private void pasteCommand() {
        ClipboardManager clipboard = getSystemService(ClipboardManager.class);
        if (clipboard == null || !clipboard.hasPrimaryClip() || clipboard.getPrimaryClip() == null
                || clipboard.getPrimaryClip().getItemCount() == 0) {
            Toast.makeText(this, "Clipboard is empty or unavailable", Toast.LENGTH_LONG).show();
            return;
        }
        CharSequence text = clipboard.getPrimaryClip().getItemAt(0).coerceToText(this);
        if (text == null || text.toString().trim().isEmpty()) {
            Toast.makeText(this, "Clipboard does not contain text", Toast.LENGTH_LONG).show();
            return;
        }
        command.setText(text.toString().trim());
        Toast.makeText(this, "Command pasted", Toast.LENGTH_SHORT).show();
    }

    private void saveAndFinish() {
        String commandValue = command.getText().toString().trim();
        if (commandValue.isEmpty()) {
            Toast.makeText(this, "Enter a command", Toast.LENGTH_SHORT).show();
            return;
        }
        Bundle config = new Bundle();
        config.putString(TaskerProtocol.KEY_COMMAND, commandValue);
        config.putString(TaskerProtocol.KEY_WORKDIR, workdir.getText().toString().trim());
        config.putBoolean(TaskerProtocol.KEY_BACKGROUND, background.isChecked());
        config.putBoolean(TaskerProtocol.KEY_OVERLAY, overlay.isChecked());
        config.putBoolean(TaskerProtocol.KEY_ROOT_BRIDGE, rootBridge.isChecked());
        config.putString(TaskerProtocol.KEY_AUTH_TOKEN, TaskerAuthorization.token(this));
        Intent result = new Intent();
        TaskerProtocol.decorateEditResult(result, config);
        setResult(RESULT_OK, result);
        finish();
    }
}
