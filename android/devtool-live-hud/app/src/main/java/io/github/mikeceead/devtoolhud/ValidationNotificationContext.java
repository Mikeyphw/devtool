package io.github.mikeceead.devtoolhud;

import java.util.concurrent.ConcurrentHashMap;

final class ValidationNotificationContext {
    private static final ConcurrentHashMap<String, ValidationSessionRecord> SESSIONS = new ConcurrentHashMap<>();
    private ValidationNotificationContext() {}
    static void put(ValidationSessionRecord session) { if (session != null && !session.sessionId.isEmpty()) SESSIONS.put(session.sessionId, session); }
    static ValidationSessionRecord lookup(String sessionId) { return sessionId == null ? null : SESSIONS.get(sessionId); }
}
