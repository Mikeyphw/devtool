package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.graphics.Color;
import android.os.Build;

final class AppPalette {
    final int background;
    final int surface;
    final int surfaceRaised;
    final int text;
    final int muted;
    final int green;
    final int cyan;
    final int yellow;
    final int red;
    final int orange;

    private AppPalette(int background, int surface, int surfaceRaised, int text, int muted,
                       int green, int cyan, int yellow, int red, int orange) {
        this.background = background;
        this.surface = surface;
        this.surfaceRaised = surfaceRaised;
        this.text = text;
        this.muted = muted;
        this.green = green;
        this.cyan = cyan;
        this.yellow = yellow;
        this.red = red;
        this.orange = orange;
    }

    static AppPalette resolve(Context context) {
        if (AppSettings.useMonet(context) && Build.VERSION.SDK_INT >= 31) {
            int bg = systemColor(context, "system_neutral1_1000", Color.rgb(11, 15, 20));
            int surface = systemColor(context, "system_neutral1_900", Color.rgb(18, 25, 35));
            int raised = systemColor(context, "system_neutral1_800", Color.rgb(25, 34, 46));
            int text = systemColor(context, "system_neutral1_50", Color.rgb(238, 244, 255));
            int muted = systemColor(context, "system_neutral2_200", Color.rgb(142, 160, 180));
            int primary = systemColor(context, "system_accent1_200", Color.rgb(94, 228, 155));
            int secondary = systemColor(context, "system_accent2_200", Color.rgb(104, 216, 255));
            int tertiary = systemColor(context, "system_accent3_200", Color.rgb(255, 209, 102));
            return new AppPalette(bg, surface, raised, text, muted, primary, secondary, tertiary,
                    Color.rgb(255, 105, 120), Color.rgb(255, 176, 92));
        }
        return new AppPalette(
                Color.rgb(11, 15, 20),
                Color.rgb(18, 25, 35),
                Color.rgb(25, 34, 46),
                Color.rgb(238, 244, 255),
                Color.rgb(142, 160, 180),
                Color.rgb(94, 228, 155),
                Color.rgb(104, 216, 255),
                Color.rgb(255, 209, 102),
                Color.rgb(255, 105, 120),
                Color.rgb(255, 176, 92));
    }

    private static int systemColor(Context context, String name, int fallback) {
        int id = context.getResources().getIdentifier(name, "color", "android");
        if (id == 0) return fallback;
        try {
            return context.getColor(id);
        } catch (RuntimeException ignored) {
            return fallback;
        }
    }
}
