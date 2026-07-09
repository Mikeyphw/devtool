package io.github.mikeceead.devtoolhud;

import android.Manifest;
import android.app.Activity;
import android.app.NotificationManager;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.View;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

public final class MainActivity extends Activity {
    private static final int REQUEST_NOTIFICATIONS = 41;
    private static final int REQUEST_TERMUX = 42;

    private UiKit ui;
    private TextView statusView;
    private TextView tokenView;
    private EditText commandInput;
    private EditText workdirInput;
    private Switch backgroundToggle;
    private Switch overlayDefaultToggle;
    private Switch monetToggle;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        new NotificationController(this);
        rebuildUi();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (statusView != null) refreshStatus();
    }

    private void rebuildUi() {
        ui = new UiKit(this);
        getWindow().setStatusBarColor(ui.palette.background);
        getWindow().setNavigationBarColor(ui.palette.background);
        setContentView(buildUi());
        refreshStatus();
    }

    private View buildUi() {
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(ui.dp(20), ui.dp(18), ui.dp(20), ui.dp(28));
        content.setBackgroundColor(ui.palette.background);

        TextView title = ui.text("Devtool Live HUD", 28, ui.palette.text, true);
        content.addView(title);
        TextView subtitle = ui.text(
                "Native build progress, a user-enabled floating HUD, direct Termux execution and a Tasker action plug-in.",
                15, ui.palette.muted, false);
        subtitle.setPadding(0, ui.dp(6), 0, ui.dp(14));
        content.addView(subtitle);

        LinearLayout statusCard = ui.surface();
        statusView = ui.text("", 14, ui.palette.green, false);
        statusCard.addView(statusView);
        content.addView(statusCard);

        content.addView(ui.sectionTitle("Appearance"));
        LinearLayout appearance = ui.surface();
        monetToggle = ui.toggle("Use Android Monet colors", AppSettings.useMonet(this));
        monetToggle.setOnCheckedChangeListener((button, checked) -> {
            AppSettings.setUseMonet(this, checked);
            rebuildUi();
        });
        appearance.addView(monetToggle);
        appearance.addView(ui.text(
                "Off uses the exact desktop GTK palette. On follows the device wallpaper palette on Android 12+.",
                13, ui.palette.muted, false));
        content.addView(appearance);

        content.addView(ui.sectionTitle("Notifications and overlay"));
        LinearLayout hud = ui.surface();
        hud.addView(ui.button("Allow notifications", ui.palette.green, v -> requestNotifications()));
        if (Build.VERSION.SDK_INT >= 36) {
            hud.addView(ui.secondaryButton("Promoted Live Update settings", v -> openPromotedSettings()));
        }
        hud.addView(ui.secondaryButton("Notification settings", v -> openNotificationSettings()));
        hud.addView(ui.secondaryButton("Allow display over apps", v -> openOverlaySettings()));
        hud.addView(ui.button("Enable floating HUD", ui.palette.cyan, v -> enableOverlay()));
        hud.addView(ui.secondaryButton("Disable floating HUD", v -> {
            OverlayHudService.setEnabled(this, false);
            refreshStatus();
        }));
        hud.addView(ui.secondaryButton("Test notification", v ->
                new NotificationController(this).post(HudUpdate.sample())));
        hud.addView(ui.secondaryButton("Test floating HUD", v -> {
            if (!OverlayHudService.hasPermission(this)) {
                openOverlaySettings();
                return;
            }
            OverlayHudService.setEnabled(this, true);
            OverlayHudService.publish(this, HudUpdate.sampleOverlay());
        }));
        content.addView(hud);

        content.addView(ui.sectionTitle("Run in Termux"));
        LinearLayout termux = ui.surface();
        commandInput = ui.input(AppSettings.command(this), "devtool command", true);
        workdirInput = ui.input(AppSettings.workdir(this), "Termux working directory", false);
        backgroundToggle = ui.toggle("Run in background (separate stdout/stderr)",
                AppSettings.runInBackground(this));
        overlayDefaultToggle = ui.toggle("Request floating HUD for app/Tasker runs",
                AppSettings.overlayByDefault(this));
        termux.addView(commandInput);
        termux.addView(workdirInput);
        termux.addView(backgroundToggle);
        termux.addView(overlayDefaultToggle);
        termux.addView(ui.button("Save command settings", ui.palette.green, v -> saveCommandSettings()));
        termux.addView(ui.button("Run now", ui.palette.cyan, v -> runConfiguredCommand()));
        termux.addView(ui.secondaryButton("Grant Termux command permission", v -> requestTermuxPermission()));
        termux.addView(ui.text(
                "Termux must have allow-external-apps=true in ~/.termux/termux.properties. Tasker will list “Devtool Live HUD” under Plug-in actions.",
                13, ui.palette.muted, false));
        content.addView(termux);

        content.addView(ui.sectionTitle("Secure bridge"));
        LinearLayout bridge = ui.surface();
        tokenView = ui.text(BridgeSecrets.getOrCreateToken(this), 13, ui.palette.text, false);
        tokenView.setTextIsSelectable(true);
        tokenView.setTypeface(android.graphics.Typeface.MONOSPACE);
        tokenView.setPadding(ui.dp(12), ui.dp(10), ui.dp(12), ui.dp(10));
        tokenView.setBackground(ui.flat(ui.palette.surfaceRaised, 10));
        bridge.addView(tokenView);
        bridge.addView(ui.secondaryButton("Copy bridge token", v -> copyToken()));
        bridge.addView(ui.secondaryButton("Regenerate bridge token", v -> {
            tokenView.setText(BridgeSecrets.regenerateToken(this));
            Toast.makeText(this, "Token regenerated; update the Termux token file", Toast.LENGTH_LONG).show();
        }));
        content.addView(bridge);

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(ui.palette.background);
        scroll.addView(content);
        return scroll;
    }

    private void saveCommandSettings() {
        AppSettings.setCommand(this, commandInput.getText().toString());
        AppSettings.setWorkdir(this, workdirInput.getText().toString());
        AppSettings.setRunInBackground(this, backgroundToggle.isChecked());
        AppSettings.setOverlayByDefault(this, overlayDefaultToggle.isChecked());
        Toast.makeText(this, "Command settings saved", Toast.LENGTH_SHORT).show();
    }

    private void runConfiguredCommand() {
        saveCommandSettings();
        TermuxCommandRunner.LaunchResult result = TermuxCommandRunner.run(
                this, AppSettings.command(this), AppSettings.workdir(this),
                AppSettings.runInBackground(this), AppSettings.overlayByDefault(this), null);
        Toast.makeText(this,
                result.started ? "Started in Termux · run " + result.executionId : result.error,
                result.started ? Toast.LENGTH_SHORT : Toast.LENGTH_LONG).show();
    }

    private void refreshStatus() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        boolean notifications = manager.areNotificationsEnabled();
        String promoted = Build.VERSION.SDK_INT >= 36
                ? (canPostPromotedNotifications(manager) ? "enabled" : "not enabled")
                : "Android 16+";
        statusView.setText(
                "Notifications  " + yesNo(notifications) +
                "\nPromoted updates  " + promoted +
                "\nOverlay permission  " + yesNo(OverlayHudService.hasPermission(this)) +
                "\nFloating HUD  " + (OverlayHudService.isRunning() ? "running" :
                        (OverlayHudService.isEnabled(this) ? "enabled" : "disabled")) +
                "\nTermux installed  " + yesNo(TermuxCommandRunner.isInstalled(this)) +
                "\nTermux command permission  " + yesNo(TermuxCommandRunner.hasPermission(this)));
        if (tokenView != null) tokenView.setText(BridgeSecrets.getOrCreateToken(this));
    }

    private static String yesNo(boolean value) {
        return value ? "ready" : "needed";
    }

    private boolean canPostPromotedNotifications(NotificationManager manager) {
        try {
            Object value = NotificationManager.class.getMethod("canPostPromotedNotifications").invoke(manager);
            return Boolean.TRUE.equals(value);
        } catch (ReflectiveOperationException ignored) {
            return false;
        }
    }

    private void requestNotifications() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQUEST_NOTIFICATIONS);
        } else {
            openNotificationSettings();
        }
    }

    private void requestTermuxPermission() {
        if (Build.VERSION.SDK_INT >= 23 && !TermuxCommandRunner.hasPermission(this)) {
            requestPermissions(new String[]{TermuxCommandRunner.RUN_COMMAND_PERMISSION}, REQUEST_TERMUX);
        } else {
            openOwnAppDetails();
        }
    }

    private void enableOverlay() {
        if (!OverlayHudService.hasPermission(this)) {
            openOverlaySettings();
            return;
        }
        OverlayHudService.setEnabled(this, true);
        refreshStatus();
    }

    private void openOverlaySettings() {
        startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:" + getPackageName())));
    }

    private void openNotificationSettings() {
        Intent intent = new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName());
        startActivity(intent);
    }

    private void openPromotedSettings() {
        Intent intent = new Intent("android.settings.APP_NOTIFICATION_PROMOTION_SETTINGS")
                .setData(Uri.parse("package:" + getPackageName()));
        try {
            startActivity(intent);
        } catch (RuntimeException ignored) {
            openNotificationSettings();
        }
    }

    private void openOwnAppDetails() {
        startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:" + getPackageName())));
    }

    private void copyToken() {
        ClipboardManager clipboard = getSystemService(ClipboardManager.class);
        clipboard.setPrimaryClip(ClipData.newPlainText(
                "Devtool Live HUD token", BridgeSecrets.getOrCreateToken(this)));
        Toast.makeText(this, "Token copied", Toast.LENGTH_SHORT).show();
    }
}
