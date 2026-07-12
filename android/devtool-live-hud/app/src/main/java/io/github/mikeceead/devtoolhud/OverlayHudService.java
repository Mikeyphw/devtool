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


/** Optional, user-enabled floating HUD shown above other apps. */
public final class OverlayHudService extends Service {
    private static final String PREFS = "overlay_hud";
    private static final String KEY_ENABLED = "enabled";
    private static final String CHANNEL_ID = "devtool_overlay_service_v1";
    private static final int SERVICE_NOTIFICATION_ID = 7201;
    private static final long TERMINAL_LINGER_MS = 10_000L;

    static final String ACTION_START = "io.github.mikeceead.devtoolhud.OVERLAY_START";
    static final String ACTION_STOP = "io.github.mikeceead.devtoolhud.OVERLAY_STOP";

    private static volatile OverlayHudService instance;
    private static volatile HudUpdate pendingUpdate;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private HudUpdate currentRun;
    private Runnable removal;

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
            pendingUpdate = update;
            // Do not start a foreground service from this background receiver.
            // Android 15+ may reject it unless an overlay is already visible.
            // The progress notification exposes an explicit “Show HUD” action.
        }
    }

    static void cancel(String runId) {
        HudUpdate pending = pendingUpdate;
        if (pending != null && (runId == null || runId.isEmpty() || pending.runId.equals(runId))) {
            pendingUpdate = null;
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
        HudUpdate pending = pendingUpdate;
        pendingUpdate = null;
        if (pending != null) postUpdate(pending);
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
        if (removal != null) handler.removeCallbacks(removal);
        removal = null;
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
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void postUpdate(HudUpdate update) {
        handler.post(() -> {
            if (removal != null) handler.removeCallbacks(removal);
            removal = null;
            currentRun = update;
            render();
            if (update.isTerminal()) {
                final String completedRunId = update.runId;
                removal = () -> {
                    if (currentRun != null && currentRun.runId.equals(completedRunId)) currentRun = null;
                    removal = null;
                    render();
                };
                handler.postDelayed(removal, TERMINAL_LINGER_MS);
            }
        });
    }

    private void removeRun(String runId) {
        handler.post(() -> {
            if (currentRun == null || runId == null || runId.isEmpty() || currentRun.runId.equals(runId)) {
                if (removal != null) handler.removeCallbacks(removal);
                removal = null;
                currentRun = null;
                render();
            }
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

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);

        headerTitle = label("Devtool HUD", 15, palette.text, true);
        header.addView(headerTitle, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        headerCount = label("Ready", 12, palette.muted, false);
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

        int width = Math.min(dp(380), getResources().getDisplayMetrics().widthPixels - dp(24));
        windowParams = new WindowManager.LayoutParams(
                width,
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT);
        windowParams.gravity = Gravity.TOP | Gravity.END;
        windowParams.x = dp(12);
        windowParams.y = dp(72);
        windowParams.windowAnimations = android.R.style.Animation_Toast;

        try {
            windowManager.addView(overlayRoot, windowParams);
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
                windowParams.x = Math.max(0, dragStartX - Math.round(event.getRawX() - dragStartRawX));
                windowParams.y = Math.max(0, dragStartY + Math.round(event.getRawY() - dragStartRawY));
                try {
                    windowManager.updateViewLayout(overlayRoot, windowParams);
                } catch (RuntimeException ignored) {
                }
                return true;
            case MotionEvent.ACTION_UP:
                view.performClick();
                return true;
            default:
                return false;
        }
    }

    private void render() {
        if (cardsContainer == null) return;
        cardsContainer.removeAllViews();
        if (currentRun == null) {
            headerCount.setText("Ready");
            TextView waiting = label("Waiting for a devtool run", 13, palette.muted, false);
            waiting.setPadding(0, dp(10), 0, 0);
            cardsContainer.addView(waiting);
            return;
        }
        headerCount.setText(currentRun.isTerminal() ? "Complete" : "Running");
        cardsContainer.addView(card(currentRun));
    }

    private View card(HudUpdate update) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(10), dp(9), dp(10), dp(9));
        int accent = statusColor(update);
        card.setBackground(rounded(palette.surface, 13, Color.TRANSPARENT, 0));
        LinearLayout.LayoutParams cardParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        cardParams.setMargins(0, dp(9), 0, 0);
        card.setLayoutParams(cardParams);

        LinearLayout titleRow = new LinearLayout(this);
        titleRow.setOrientation(LinearLayout.HORIZONTAL);
        titleRow.setGravity(Gravity.CENTER_VERTICAL);
        TextView title = label(update.title(), 14, palette.text, true);
        title.setMaxLines(1);
        titleRow.addView(title, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        TextView percent = label(update.criticalText(), 13, accent, true);
        titleRow.addView(percent);
        card.addView(titleRow);

        TextView detail = label(update.detail, 12, palette.text, false);
        detail.setMaxLines(2);
        detail.setPadding(0, dp(4), 0, dp(6));
        card.addView(detail);

        ProgressBar overallProgress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        overallProgress.setMax(100);
        overallProgress.setIndeterminate(update.indeterminate && update.activeStep() == null && !update.isTerminal());
        overallProgress.setProgress(update.isTerminal() ? 100 : update.displayProgress(), false);
        if (Build.VERSION.SDK_INT >= 21) {
            overallProgress.setProgressTintList(android.content.res.ColorStateList.valueOf(accent));
            overallProgress.setIndeterminateTintList(android.content.res.ColorStateList.valueOf(accent));
        }
        card.addView(overallProgress, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(8)));

        LinearLayout pipeline = new LinearLayout(this);
        pipeline.setOrientation(LinearLayout.VERTICAL);
        pipeline.setPadding(0, dp(2), 0, dp(2));
        LinearLayout row = null;
        int rowCount = 0;
        for (HudUpdate.Step step : update.steps) {
            if (row == null || rowCount == 4) {
                row = new LinearLayout(this);
                row.setOrientation(LinearLayout.HORIZONTAL);
                pipeline.addView(row, new LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
                rowCount = 0;
            }
            LinearLayout segment = new LinearLayout(this);
            segment.setOrientation(LinearLayout.VERTICAL);
            segment.setPadding(dp(2), 0, dp(2), dp(3));
            TextView stepLabel = label(step.label.toUpperCase(java.util.Locale.ROOT), 9, stepColor(step), true);
            stepLabel.setGravity(Gravity.CENTER);
            stepLabel.setMaxLines(1);
            stepLabel.setEllipsize(android.text.TextUtils.TruncateAt.END);
            segment.addView(stepLabel, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
            ProgressBar stepProgress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
            stepProgress.setMax(100);
            stepProgress.setIndeterminate(step.running() && step.progress <= 0);
            stepProgress.setProgress((step.succeeded() || step.failed() || step.skipped()) ? 100 : step.progress, false);
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
            while (rowCount < 4) {
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
        TextView meta = label(metrics.toString(), 11, palette.muted, false);
        meta.setPadding(0, dp(6), 0, 0);
        card.addView(meta);
        return card;
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

    private TextView label(String value, int sp, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setText(HudUpdate.plainText(value));
        view.setTextSize(sp);
        view.setTextColor(color);
        view.setTypeface(Typeface.DEFAULT, bold ? Typeface.BOLD : Typeface.NORMAL);
        view.setLineSpacing(0, 1.08f);
        return view;
    }

    private Button compactButton(String value) {
        Button button = new Button(this);
        button.setText(value);
        button.setTextSize(17);
        button.setTextColor(palette.text);
        button.setAllCaps(false);
        button.setMinWidth(dp(38));
        button.setMinimumWidth(dp(38));
        button.setMinHeight(dp(34));
        button.setMinimumHeight(dp(34));
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

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
