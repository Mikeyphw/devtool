package io.github.mikeceead.devtoolhud;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.provider.Settings;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Optional, user-enabled floating HUD shown above other apps. */
public final class OverlayHudService extends Service {
    private static final String PREFS = "overlay_hud";
    private static final String KEY_ENABLED = "enabled";
    private static final String KEY_X = "x";
    private static final String KEY_Y = "y";
    private static final int DEFAULT_X_DP = 12;
    private static final int DEFAULT_Y_DP = 72;
    private static final String CHANNEL_ID = "devtool_overlay_service_v1";
    private static final int SERVICE_NOTIFICATION_ID = 7201;
    private static final long TERMINAL_LINGER_MS = 10_000L;

    static final String ACTION_START = "io.github.mikeceead.devtoolhud.OVERLAY_START";
    static final String ACTION_STOP = "io.github.mikeceead.devtoolhud.OVERLAY_STOP";

    private static volatile OverlayHudService instance;
    private static final Map<String, HudUpdate> pendingUpdates = new LinkedHashMap<>();

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final LinkedHashMap<String, HudUpdate> currentRuns = new LinkedHashMap<>();
    private final Map<String, Runnable> removals = new LinkedHashMap<>();

    private WindowManager windowManager;
    private WindowManager.LayoutParams windowParams;
    private LinearLayout overlayRoot;
    private LinearLayout cardsContainer;
    private TextView headerTitle;
    private TextView headerCount;
    private boolean explicitlyStopped;
    private AppPalette palette;
    private boolean collapsed;
    private float dragStartRawX;
    private float dragStartRawY;
    private int dragStartX;
    private int dragStartY;

    static boolean hasPermission(Context context) {
        return Build.VERSION.SDK_INT < 23 || Settings.canDrawOverlays(context);
    }

    static boolean isEnabled(Context context) {
        return context.getSharedPreferences(PREFS, MODE_PRIVATE).getBoolean(KEY_ENABLED, false);
    }

    static boolean isRunning() {
        return instance != null;
    }

    static void setEnabled(Context context, boolean enabled) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS, MODE_PRIVATE);
        prefs.edit().putBoolean(KEY_ENABLED, enabled).apply();
        Intent intent = new Intent(context, OverlayHudService.class)
                .setAction(enabled ? ACTION_START : ACTION_STOP);
        if (enabled) {
            if (!hasPermission(context)) return;
            try {
                if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent);
                else context.startService(intent);
            } catch (RuntimeException ignored) {
                // Android may reject a background foreground-service start. The
                // setup activity can always start it while visible.
            }
        } else {
            context.stopService(intent);
        }
    }

    static void publish(Context context, HudUpdate update) {
        if (!update.overlayRequested) {
            cancel(update.runId);
            return;
        }
        OverlayHudService active = instance;
        if (active != null) {
            active.postUpdate(update);
            return;
        }
        if (isEnabled(context) && hasPermission(context)) {
            synchronized (pendingUpdates) {
                pendingUpdates.remove(update.runId);
                pendingUpdates.put(update.runId, update);
            }
            // Do not start a foreground service from this background receiver.
            // The progress notification exposes an explicit Show HUD action.
        }
    }

    static void cancel(String runId) {
        synchronized (pendingUpdates) {
            if (runId == null || runId.isEmpty()) pendingUpdates.clear();
            else pendingUpdates.remove(runId);
        }
        OverlayHudService active = instance;
        if (active != null) active.removeRun(runId);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        palette = AppPalette.resolve(this);
        if (!hasPermission(this)) {
            stopSelf();
            return;
        }
        ensureServiceChannel();
        Notification notification = buildServiceNotification();
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(SERVICE_NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(SERVICE_NOTIFICATION_ID, notification);
        }
        createOverlay();
        List<HudUpdate> pending;
        synchronized (pendingUpdates) {
            pending = new ArrayList<>(pendingUpdates.values());
            pendingUpdates.clear();
        }
        for (HudUpdate update : pending) postUpdate(update);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_START : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            explicitlyStopped = true;
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putBoolean(KEY_ENABLED, false).apply();
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!hasPermission(this) || !isEnabled(this)) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (overlayRoot == null) createOverlay();
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        instance = null;
        for (Runnable removal : removals.values()) handler.removeCallbacks(removal);
        removals.clear();
        currentRuns.clear();
        if (windowManager != null && overlayRoot != null) {
            try {
                windowManager.removeView(overlayRoot);
            } catch (RuntimeException ignored) {
            }
        }
        overlayRoot = null;
        if (explicitlyStopped) {
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putBoolean(KEY_ENABLED, false).apply();
        }
        super.onDestroy();
    }


    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        handler.post(() -> {
            if (windowManager == null || windowParams == null || overlayRoot == null) return;
            int preferredWidth = largeText() ? dp(520) : dp(380);
            windowParams.width = Math.min(preferredWidth, getResources().getDisplayMetrics().widthPixels - dp(24));
            clampAndUpdateOverlayPosition();
        });
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void postUpdate(HudUpdate update) {
        handler.post(() -> {
            Runnable previousRemoval = removals.remove(update.runId);
            if (previousRemoval != null) handler.removeCallbacks(previousRemoval);
            currentRuns.remove(update.runId);
            currentRuns.put(update.runId, update);
            render();
            if (update.isTerminal()) {
                final String completedRunId = update.runId;
                Runnable removal = () -> {
                    currentRuns.remove(completedRunId);
                    removals.remove(completedRunId);
                    render();
                };
                removals.put(completedRunId, removal);
                handler.postDelayed(removal, TERMINAL_LINGER_MS);
            }
        });
    }

    private void removeRun(String runId) {
        handler.post(() -> {
            if (runId == null || runId.isEmpty()) {
                for (Runnable removal : removals.values()) handler.removeCallbacks(removal);
                removals.clear();
                currentRuns.clear();
            } else {
                Runnable removal = removals.remove(runId);
                if (removal != null) handler.removeCallbacks(removal);
                currentRuns.remove(runId);
            }
            render();
        });
    }

    private void createOverlay() {
        if (overlayRoot != null || !hasPermission(this)) return;
        windowManager = getSystemService(WindowManager.class);

        overlayRoot = new LinearLayout(this);
        overlayRoot.setOrientation(LinearLayout.VERTICAL);
        overlayRoot.setPadding(dp(12), dp(10), dp(12), dp(12));
        overlayRoot.setBackground(rounded(palette.background, 18, Color.TRANSPARENT, 0));
        overlayRoot.setElevation(dp(12));
        overlayRoot.setContentDescription("Devtool floating progress HUD");
        overlayRoot.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);

        headerTitle = label("Devtool HUD", UiTypography.Role.CARD_TITLE, palette.text);
        if (Build.VERSION.SDK_INT >= 28) headerTitle.setAccessibilityHeading(true);
        header.addView(headerTitle, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        headerCount = label("Ready", UiTypography.Role.LABEL, palette.muted);
        headerCount.setPadding(dp(8), 0, dp(8), 0);
        header.addView(headerCount);

        Button collapseButton = compactButton("–");
        collapseButton.setContentDescription("Collapse or expand overlay HUD");
        collapseButton.setOnClickListener(v -> {
            collapsed = !collapsed;
            collapseButton.setText(collapsed ? "+" : "–");
            cardsContainer.setVisibility(collapsed ? View.GONE : View.VISIBLE);
        });
        header.addView(collapseButton);

        Button closeButton = compactButton("×");
        closeButton.setContentDescription("Disable overlay HUD");
        closeButton.setOnClickListener(v -> setEnabled(this, false));
        header.addView(closeButton);

        header.setOnTouchListener(this::dragOverlay);
        overlayRoot.addView(header);

        cardsContainer = new LinearLayout(this);
        cardsContainer.setOrientation(LinearLayout.VERTICAL);
        overlayRoot.addView(cardsContainer, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        int preferredWidth = largeText() ? dp(520) : dp(380);
        int width = Math.min(preferredWidth, getResources().getDisplayMetrics().widthPixels - dp(24));
        windowParams = new WindowManager.LayoutParams(
                width,
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT);
        windowParams.gravity = Gravity.TOP | Gravity.END;
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        windowParams.x = clampInt(prefs.getInt(KEY_X, dp(DEFAULT_X_DP)), 0, maxOverlayX(width));
        windowParams.y = clampInt(prefs.getInt(KEY_Y, dp(DEFAULT_Y_DP)), 0, maxOverlayY(0));
        windowParams.windowAnimations = android.R.style.Animation_Toast;

        try {
            windowManager.addView(overlayRoot, windowParams);
            overlayRoot.post(this::clampAndUpdateOverlayPosition);
            render();
        } catch (RuntimeException ignored) {
            overlayRoot = null;
            stopSelf();
        }
    }

    private boolean dragOverlay(View view, MotionEvent event) {
        if (windowManager == null || windowParams == null) return false;
        switch (event.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                dragStartRawX = event.getRawX();
                dragStartRawY = event.getRawY();
                dragStartX = windowParams.x;
                dragStartY = windowParams.y;
                return true;
            case MotionEvent.ACTION_MOVE:
                // Gravity.END makes positive x move inward from the right edge.
                windowParams.x = clampInt(dragStartX - Math.round(event.getRawX() - dragStartRawX), 0, maxOverlayX(windowParams.width));
                windowParams.y = clampInt(dragStartY + Math.round(event.getRawY() - dragStartRawY), 0, maxOverlayY(overlayRoot.getHeight()));
                try {
                    windowManager.updateViewLayout(overlayRoot, windowParams);
                } catch (RuntimeException ignored) {
                }
                return true;
            case MotionEvent.ACTION_UP:
                view.performClick();
                saveHudPosition();
                return true;
            case MotionEvent.ACTION_CANCEL:
                saveHudPosition();
                return true;
            default:
                return false;
        }
    }


    private void clampAndUpdateOverlayPosition() {
        if (windowManager == null || windowParams == null || overlayRoot == null) return;
        windowParams.x = clampInt(windowParams.x, 0, maxOverlayX(windowParams.width));
        windowParams.y = clampInt(windowParams.y, 0, maxOverlayY(overlayRoot.getHeight()));
        try {
            windowManager.updateViewLayout(overlayRoot, windowParams);
        } catch (RuntimeException ignored) {
        }
        saveHudPosition();
    }

    private void saveHudPosition() {
        if (windowParams == null) return;
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                .putInt(KEY_X, windowParams.x)
                .putInt(KEY_Y, windowParams.y)
                .apply();
    }

    private int maxOverlayX(int width) {
        int displayWidth = getResources().getDisplayMetrics().widthPixels;
        int safeWidth = width > 0 ? width : (overlayRoot != null ? overlayRoot.getWidth() : dp(380));
        return Math.max(0, displayWidth - safeWidth - dp(8));
    }

    private int maxOverlayY(int height) {
        int displayHeight = getResources().getDisplayMetrics().heightPixels;
        int safeHeight = height > 0 ? height : (overlayRoot != null ? overlayRoot.getHeight() : dp(224));
        return Math.max(0, displayHeight - safeHeight - dp(8));
    }

    private int clampInt(int value, int low, int high) {
        return Math.max(low, Math.min(high, value));
    }

    private void render() {
        if (cardsContainer == null) return;
        cardsContainer.removeAllViews();
        if (currentRuns.isEmpty()) {
            headerCount.setText("Ready");
            TextView waiting = label("Waiting for a devtool run", UiTypography.Role.BODY_SMALL, palette.muted);
            waiting.setPadding(0, dp(10), 0, 0);
            cardsContainer.addView(waiting);
            return;
        }
        int active = 0;
        for (HudUpdate update : currentRuns.values()) if (!update.isTerminal()) active++;
        String countText = active > 0
                ? active + " running" + (currentRuns.size() > active ? " · " + (currentRuns.size() - active) + " complete" : "")
                : currentRuns.size() + " complete";
        headerCount.setText(countText);
        headerCount.setContentDescription(countText);
        List<HudUpdate> ordered = new ArrayList<>(currentRuns.values());
        for (int index = ordered.size() - 1; index >= 0; index--) cardsContainer.addView(card(ordered.get(index), index == ordered.size() - 1));
        cardsContainer.post(this::clampAndUpdateOverlayPosition);
    }

    private View card(HudUpdate update, boolean primaryRun) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(10), dp(9), dp(10), dp(9));
        int accent = statusColor(update);
        card.setBackground(rounded(primaryRun ? palette.surface : palette.surfaceRaised, 13, primaryRun ? accent : Color.TRANSPARENT, primaryRun ? 1 : 0));
        card.setAlpha(primaryRun ? 1.0f : 0.86f);
        card.setContentDescription(update.title() + ". " + update.status + ". " + update.detail + ". " + update.displayProgress() + " percent.");
        card.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
        LinearLayout.LayoutParams cardParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        cardParams.setMargins(0, dp(9), 0, 0);
        card.setLayoutParams(cardParams);

        TextView section = label(primaryRun ? "Current run" : "Recent run", UiTypography.Role.CAPTION, primaryRun ? accent : palette.muted);
        section.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        section.setPadding(0, 0, 0, dp(4));
        card.addView(section);

        LinearLayout titleRow = new LinearLayout(this);
        titleRow.setOrientation(LinearLayout.HORIZONTAL);
        titleRow.setGravity(Gravity.CENTER_VERTICAL);
        TextView title = label(update.title(), UiTypography.Role.CARD_TITLE, palette.text);
        title.setMaxLines(largeText() ? 2 : 1);
        titleRow.addView(title, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        TextView percent = label(update.criticalText(), primaryRun ? UiTypography.Role.CARD_TITLE : UiTypography.Role.BODY_SMALL, accent);
        titleRow.addView(percent);
        card.addView(titleRow);

        TextView statusLead = label(statusGlyph(update) + " " + update.notificationStatusLead(), UiTypography.Role.LABEL, accent);
        statusLead.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        statusLead.setPadding(0, dp(4), 0, 0);
        card.addView(statusLead);

        TextView detail = label(update.notificationStatusDetail(), UiTypography.Role.LABEL, palette.text);
        detail.setMaxLines(2);
        detail.setPadding(0, dp(1), 0, dp(6));
        card.addView(detail);

        TextView runner = label("Runner · " + update.runnerLabel() + " · " + update.environmentLabel(), UiTypography.Role.CAPTION, palette.muted);
        runner.setPadding(0, 0, 0, dp(5));
        card.addView(runner);

        ProgressBar overallProgress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        overallProgress.setMax(100);
        overallProgress.setIndeterminate(update.indeterminate && update.activeStep() == null && !update.isTerminal());
        overallProgress.setProgress(update.isTerminal() ? 100 : update.displayProgress(), false);
        overallProgress.setContentDescription(update.title() + ", " + update.displayProgress() + " percent, " + update.status);
        if (Build.VERSION.SDK_INT >= 21) {
            overallProgress.setProgressTintList(android.content.res.ColorStateList.valueOf(accent));
            overallProgress.setIndeterminateTintList(android.content.res.ColorStateList.valueOf(accent));
        }
        card.addView(overallProgress, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(8)));

        TextView pipelineHeading = label("Pipeline", UiTypography.Role.CAPTION, palette.muted);
        pipelineHeading.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        pipelineHeading.setPadding(0, dp(7), 0, dp(2));
        card.addView(pipelineHeading);

        LinearLayout pipeline = new LinearLayout(this);
        pipeline.setOrientation(LinearLayout.VERTICAL);
        pipeline.setPadding(0, dp(2), 0, dp(2));
        LinearLayout row = null;
        int rowCount = 0;
        int maxSegmentsPerRow = largeText() ? 2 : 4;
        for (HudUpdate.Step step : update.steps) {
            if (row == null || rowCount == maxSegmentsPerRow) {
                row = new LinearLayout(this);
                row.setOrientation(LinearLayout.HORIZONTAL);
                pipeline.addView(row, new LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
                rowCount = 0;
            }
            LinearLayout segment = new LinearLayout(this);
            segment.setOrientation(LinearLayout.VERTICAL);
            segment.setPadding(dp(2), 0, dp(2), dp(3));
            TextView stepLabel = label(step.label.toUpperCase(java.util.Locale.ROOT), UiTypography.Role.CAPTION, stepColor(step));
            stepLabel.setGravity(Gravity.CENTER);
            stepLabel.setMaxLines(1);
            stepLabel.setEllipsize(android.text.TextUtils.TruncateAt.END);
            segment.addView(stepLabel, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
            ProgressBar stepProgress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
            stepProgress.setMax(100);
            stepProgress.setIndeterminate(step.running() && step.progress <= 0);
            stepProgress.setProgress((step.succeeded() || step.failed() || step.skipped()) ? 100 : step.progress, false);
            stepProgress.setContentDescription(step.label + ", " + step.status + ", " + step.progress + " percent");
            if (Build.VERSION.SDK_INT >= 21) {
                stepProgress.setProgressTintList(android.content.res.ColorStateList.valueOf(stepColor(step)));
                stepProgress.setIndeterminateTintList(android.content.res.ColorStateList.valueOf(stepColor(step)));
            }
            segment.addView(stepProgress, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, dp(7)));
            row.addView(segment, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            rowCount++;
        }
        if (row != null) {
            while (rowCount < maxSegmentsPerRow) {
                android.view.View spacer = new android.view.View(this);
                row.addView(spacer, new LinearLayout.LayoutParams(0, 1, 1f));
                rowCount++;
            }
        }
        card.addView(pipeline, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        StringBuilder metrics = new StringBuilder(update.subText());
        int tests = update.passed + update.failed + update.skipped;
        if (tests > 0) metrics.append("\n").append(update.passed).append(" passed · ")
                .append(update.failed).append(" failed · ").append(update.skipped).append(" skipped");
        metrics.append("\n").append(update.errors).append(" errors · ")
                .append(update.warnings).append(" warnings");
        if (!update.commit.isEmpty()) metrics.append("\nCommit ").append(update.commit);
        TextView metricsHeading = label("Metrics", UiTypography.Role.CAPTION, palette.muted);
        metricsHeading.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        metricsHeading.setPadding(0, dp(7), 0, dp(1));
        card.addView(metricsHeading);
        TextView meta = label(metrics.toString(), UiTypography.Role.CAPTION, palette.muted);
        meta.setPadding(0, dp(2), 0, 0);
        card.addView(meta);
        return card;
    }

    private String statusGlyph(HudUpdate update) {
        if (update.succeeded()) return "✓";
        if (update.failedState()) return "✕";
        if (update.status.equals("interrupted") || update.status.equals("cancelled")) return "!";
        if (update.status.equals("waiting")) return "…";
        return "●";
    }

    private int stepColor(HudUpdate.Step step) {
        if (step.failed()) return palette.red;
        if (step.succeeded()) return palette.green;
        if (step.running()) return palette.yellow;
        if (step.skipped()) return palette.muted;
        return palette.muted;
    }

    private void ensureServiceChannel() {
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID, "Devtool overlay service", NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("Keeps the optional Devtool floating HUD available");
        channel.setSound(null, null);
        channel.enableVibration(false);
        channel.setShowBadge(false);
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
    }

    private Notification buildServiceNotification() {
        Intent open = new Intent(this, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent openIntent = PendingIntent.getActivity(this, 7201, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Intent stop = new Intent(this, ActionReceiver.class).setAction(ActionReceiver.ACTION_STOP_OVERLAY);
        PendingIntent stopIntent = PendingIntent.getBroadcast(this, 7202, stop,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_devtool_notification)
                .setContentTitle("Devtool overlay HUD enabled")
                .setContentText("Build progress can appear over other apps")
                .setCategory(Notification.CATEGORY_SERVICE)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(openIntent)
                .addAction(new Notification.Action.Builder(
                        android.graphics.drawable.Icon.createWithResource(this, R.drawable.ic_done),
                        "Disable overlay", stopIntent).build())
                .build();
    }

    private TextView label(String value, UiTypography.Role role, int color) {
        TextView view = new TextView(this);
        view.setText(HudUpdate.plainText(value));
        view.setTextColor(color);
        UiTypography.apply(view, role);
        return view;
    }

    private Button compactButton(String value) {
        Button button = new Button(this);
        button.setText(value);
        UiTypography.apply(button, UiTypography.Role.CARD_TITLE);
        button.setTextColor(palette.text);
        button.setAllCaps(false);
        button.setMinWidth(dp(48));
        button.setMinimumWidth(dp(48));
        button.setMinHeight(dp(48));
        button.setMinimumHeight(dp(48));
        button.setPadding(0, 0, 0, 0);
        button.setBackgroundColor(Color.TRANSPARENT);
        return button;
    }

    private GradientDrawable rounded(int fill, int radiusDp, int stroke, int strokeDp) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(fill);
        drawable.setCornerRadius(dp(radiusDp));
        if (strokeDp > 0) drawable.setStroke(dp(strokeDp), stroke);
        return drawable;
    }

    private int statusColor(HudUpdate update) {
        if (palette == null) palette = AppPalette.resolve(this);
        if (update.succeeded()) return palette.green;
        if (update.failedState()) return palette.red;
        if (update.status.equals("interrupted") || update.status.equals("cancelled")) return palette.orange;
        if (update.status.equals("waiting")) return palette.muted;
        return palette.yellow;
    }

    private boolean largeText() {
        return getResources().getConfiguration().fontScale >= 1.3f;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
