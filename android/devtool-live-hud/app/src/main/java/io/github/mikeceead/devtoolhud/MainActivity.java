package io.github.mikeceead.devtoolhud;

import android.Manifest;
import android.app.Activity;
import android.app.NotificationManager;
import android.content.ClipboardManager;
import android.content.ClipData;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

public final class MainActivity extends Activity {
    private static final int REQUEST_NOTIFICATIONS = 41;
    private static final String PROMOTION_SETTINGS = "android.settings.APP_NOTIFICATION_PROMOTION_SETTINGS";

    private TextView tokenView;
    private TextView statusView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        new NotificationController(this);
        setContentView(buildUi());
        refresh();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (OverlayHudService.isEnabled(this) && OverlayHudService.hasPermission(this)) {
            OverlayHudService.setEnabled(this, true);
        }
        if (statusView != null) refresh();
    }

    private View buildUi() {
        int pad = dp(20);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(pad, pad, pad, pad);
        content.setBackgroundColor(Color.rgb(16, 24, 32));

        TextView title = text("Devtool Live HUD", 28, Color.WHITE);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        content.addView(title);

        TextView subtitle = text(
                "Native Android progress notifications with an optional draggable HUD that can appear over other apps.",
                16, Color.rgb(190, 200, 212));
        subtitle.setPadding(0, dp(8), 0, dp(20));
        content.addView(subtitle);

        statusView = text("", 15, Color.rgb(94, 228, 155));
        statusView.setPadding(0, 0, 0, dp(16));
        content.addView(statusView);

        content.addView(section("Notifications"));
        content.addView(button("Allow notifications", v -> requestNotifications()));
        if (Build.VERSION.SDK_INT >= 36) {
            content.addView(button("Enable promoted Live Updates", v -> openPromotionSettings()));
        }
        content.addView(button("Notification settings", v -> openNotificationSettings()));
        content.addView(button("Send test progress notification", v -> {
            new NotificationController(this).post(HudUpdate.sample());
            Toast.makeText(this, "Test notification posted", Toast.LENGTH_SHORT).show();
        }));

        content.addView(section("Overlay HUD (optional)"));
        TextView overlayInfo = text(
                "The overlay is off by default. Grant display-over-apps permission, enable the service here, then run devtool with --hud-overlay. The notification remains available as a fallback.",
                14, Color.rgb(190, 200, 212));
        overlayInfo.setPadding(0, 0, 0, dp(6));
        content.addView(overlayInfo);
        content.addView(button("Allow display over other apps", v -> openOverlaySettings()));
        content.addView(button("Enable overlay HUD", v -> enableOverlay()));
        content.addView(button("Disable overlay HUD", v -> {
            OverlayHudService.setEnabled(this, false);
            refresh();
            Toast.makeText(this, "Overlay HUD disabled", Toast.LENGTH_SHORT).show();
        }));
        content.addView(button("Send test overlay", v -> sendTestOverlay()));

        content.addView(section("Bridge token"));
        tokenView = text("", 14, Color.WHITE);
        tokenView.setTypeface(Typeface.MONOSPACE);
        tokenView.setTextIsSelectable(true);
        tokenView.setPadding(dp(12), dp(12), dp(12), dp(12));
        tokenView.setBackgroundColor(Color.rgb(31, 43, 55));
        content.addView(tokenView);
        content.addView(button("Copy token", v -> copyToken()));
        content.addView(button("Regenerate token", v -> regenerateToken()));

        content.addView(section("Termux setup"));
        TextView command = text(
                "mkdir -p ~/.config/devtool\n" +
                "printf '%s\\n' 'PASTE_TOKEN_HERE' > ~/.config/devtool/android-live-hud.token\n" +
                "chmod 600 ~/.config/devtool/android-live-hud.token",
                14, Color.rgb(214, 220, 228));
        command.setTypeface(Typeface.MONOSPACE);
        command.setTextIsSelectable(true);
        command.setPadding(dp(12), dp(12), dp(12), dp(12));
        command.setBackgroundColor(Color.rgb(31, 43, 55));
        content.addView(command);

        TextView note = text(
                "Android and the device manufacturer control Live Update promotion. Overlay permission is separate and only used when you explicitly enable the floating HUD.",
                14, Color.rgb(190, 200, 212));
        note.setPadding(0, dp(20), 0, dp(12));
        content.addView(note);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(content);
        return scroll;
    }

    private void refresh() {
        tokenView.setText(BridgeSecrets.getOrCreateToken(this));
        NotificationManager manager = getSystemService(NotificationManager.class);
        boolean notifications = manager.areNotificationsEnabled();
        String promoted = Build.VERSION.SDK_INT >= 36
                ? (canPostPromotedNotifications(manager) ? "enabled" : "not enabled")
                : "requires Android 16";
        String overlayPermission = OverlayHudService.hasPermission(this) ? "allowed" : "not allowed";
        String overlayState = OverlayHudService.isRunning()
                ? "running"
                : (OverlayHudService.isEnabled(this) ? "enabled, waiting to start" : "disabled");
        statusView.setText(
                "Notifications: " + (notifications ? "allowed" : "blocked") +
                "\nPromoted Live Updates: " + promoted +
                "\nDisplay over apps: " + overlayPermission +
                "\nOverlay service: " + overlayState);
    }

    private boolean canPostPromotedNotifications(NotificationManager manager) {
        try {
            Object value = NotificationManager.class
                    .getMethod("canPostPromotedNotifications")
                    .invoke(manager);
            return Boolean.TRUE.equals(value);
        } catch (ReflectiveOperationException ignored) {
            return false;
        }
    }

    private void requestNotifications() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQUEST_NOTIFICATIONS);
        } else {
            openNotificationSettings();
        }
    }

    private void openPromotionSettings() {
        Intent intent = new Intent(PROMOTION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName());
        try {
            startActivity(intent);
        } catch (Exception ignored) {
            openNotificationSettings();
        }
    }

    private void openNotificationSettings() {
        Intent intent = new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName());
        startActivity(intent);
    }

    private void openOverlaySettings() {
        Intent intent = new Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:" + getPackageName()));
        try {
            startActivity(intent);
        } catch (Exception ignored) {
            startActivity(new Intent(Settings.ACTION_SETTINGS));
        }
    }

    private void enableOverlay() {
        if (!OverlayHudService.hasPermission(this)) {
            Toast.makeText(this, "Grant display-over-apps permission first", Toast.LENGTH_LONG).show();
            openOverlaySettings();
            return;
        }
        OverlayHudService.setEnabled(this, true);
        refresh();
        Toast.makeText(this, "Overlay HUD enabled", Toast.LENGTH_SHORT).show();
    }

    private void sendTestOverlay() {
        if (!OverlayHudService.hasPermission(this)) {
            Toast.makeText(this, "Grant display-over-apps permission first", Toast.LENGTH_LONG).show();
            openOverlaySettings();
            return;
        }
        OverlayHudService.setEnabled(this, true);
        OverlayHudService.publish(this, HudUpdate.sampleOverlay());
        refresh();
        Toast.makeText(this, "Test overlay posted", Toast.LENGTH_SHORT).show();
    }

    private void copyToken() {
        String token = BridgeSecrets.getOrCreateToken(this);
        ClipboardManager clipboard = getSystemService(ClipboardManager.class);
        clipboard.setPrimaryClip(ClipData.newPlainText("Devtool Live HUD token", token));
        Toast.makeText(this, "Token copied", Toast.LENGTH_SHORT).show();
    }

    private void regenerateToken() {
        tokenView.setText(BridgeSecrets.regenerateToken(this));
        Toast.makeText(this, "Token regenerated; update the Termux token file", Toast.LENGTH_LONG).show();
    }

    private TextView section(String label) {
        TextView view = text(label, 18, Color.WHITE);
        view.setTypeface(Typeface.DEFAULT_BOLD);
        view.setPadding(0, dp(22), 0, dp(8));
        return view;
    }

    private TextView text(String value, int sp, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(sp);
        view.setTextColor(color);
        view.setLineSpacing(0, 1.12f);
        return view;
    }

    private Button button(String label, View.OnClickListener listener) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setOnClickListener(listener);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, dp(8), 0, 0);
        button.setLayoutParams(params);
        return button;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
