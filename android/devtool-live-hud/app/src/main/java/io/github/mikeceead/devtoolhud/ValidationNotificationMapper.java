package io.github.mikeceead.devtoolhud;

final class ValidationNotificationMapper {
    private ValidationNotificationMapper() {}
    static HudUpdate map(ValidationSessionRecord session) {
        ValidationNotificationContext.put(session);
        return HudUpdate.validation(session);
    }
}
