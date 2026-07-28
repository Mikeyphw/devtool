package io.github.mikeceead.devtoolhud;

import android.content.Context;

final class ValidationNotificationController {
    private final Context context;
    ValidationNotificationController(Context context) { this.context = context.getApplicationContext(); }
    void post(ValidationSessionRecord session) {
        if (session == null || session.sessionId.isEmpty()) return;
        new NotificationController(context).post(ValidationNotificationMapper.map(session));
    }
    void cancel(String sessionId) {
        new NotificationController(context).cancel("validation:" + sessionId);
    }
}
