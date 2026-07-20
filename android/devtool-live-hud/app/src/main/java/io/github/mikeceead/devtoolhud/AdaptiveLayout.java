package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.res.Configuration;

/** Central responsive-layout policy for phones, tablets, foldables, and enlarged text. */
final class AdaptiveLayout {
    private AdaptiveLayout() {}

    static int widthDp(Context context) {
        Configuration configuration = context.getResources().getConfiguration();
        return Math.max(1, configuration.screenWidthDp);
    }

    static int heightDp(Context context) {
        Configuration configuration = context.getResources().getConfiguration();
        return Math.max(1, configuration.screenHeightDp);
    }

    static float fontScale(Context context) {
        return context.getResources().getConfiguration().fontScale;
    }

    static boolean largeText(Context context) {
        return fontScale(context) >= 1.30f;
    }

    static boolean extraLargeText(Context context) {
        return fontScale(context) >= 1.60f;
    }

    static boolean wide(Context context) {
        return widthDp(context) >= 720;
    }

    static boolean twoPane(Context context) {
        return widthDp(context) >= 840 && !extraLargeText(context);
    }

    static boolean compactWidth(Context context) {
        return widthDp(context) < 420;
    }

    static boolean verticalActions(Context context) {
        return largeText(context) || compactWidth(context);
    }

    static int navigationRailWidthDp(Context context) {
        if (extraLargeText(context)) return 196;
        return widthDp(context) >= 1000 ? 188 : 168;
    }

    static int detailRailWidthDp(Context context) {
        return widthDp(context) >= 1000 ? 196 : 176;
    }

    static int contentHorizontalPaddingDp(Context context) {
        if (widthDp(context) >= 1200) return 28;
        if (wide(context)) return 22;
        return 16;
    }
}
