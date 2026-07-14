package io.github.mikeceead.devtoolhud;

import android.graphics.Typeface;
import android.widget.TextView;

/** Semantic Android typography shared by the app, overlay HUD, and generated controls. */
final class UiTypography {
    enum Role {
        PAGE_TITLE(28f, "sans-serif-medium", 1.22f),
        LARGE_VALUE(20f, "sans-serif-medium", 1.18f),
        CARD_TITLE(16f, "sans-serif-medium", 1.25f),
        BODY(14f, "sans-serif", 1.40f),
        BODY_SMALL(13f, "sans-serif", 1.36f),
        BUTTON(14f, "sans-serif-medium", 1.20f),
        LABEL(12f, "sans-serif-medium", 1.25f),
        CAPTION(11f, "sans-serif", 1.30f),
        CODE(13f, "monospace", 1.36f),
        ICON(20f, "sans-serif-medium", 1.0f);

        final float sizeSp;
        final String family;
        final float lineHeight;

        Role(float sizeSp, String family, float lineHeight) {
            this.sizeSp = sizeSp;
            this.family = family;
            this.lineHeight = lineHeight;
        }
    }

    private UiTypography() {}

    static void apply(TextView view, Role role) {
        view.setTextSize(role.sizeSp);
        view.setTypeface(Typeface.create(role.family, Typeface.NORMAL));
        view.setLineSpacing(0f, role.lineHeight);
        view.setIncludeFontPadding(true);
    }

    static Role fromLegacySize(int sp, boolean emphasized) {
        if (sp >= 25) return Role.PAGE_TITLE;
        if (sp >= 19) return Role.LARGE_VALUE;
        if (sp >= 15) return Role.CARD_TITLE;
        if (sp >= 14) return emphasized ? Role.BUTTON : Role.BODY;
        if (sp >= 13) return Role.BODY_SMALL;
        if (sp >= 12) return Role.LABEL;
        return Role.CAPTION;
    }
}
