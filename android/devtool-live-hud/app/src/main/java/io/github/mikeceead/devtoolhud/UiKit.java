package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.Switch;
import android.widget.TextView;

final class UiKit {
    private final Context context;
    final AppPalette palette;

    UiKit(Context context) {
        this.context = context;
        this.palette = AppPalette.resolve(context);
    }

    int dp(int value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }

    boolean largeText() {
        return context.getResources().getConfiguration().fontScale >= 1.3f;
    }

    TextView text(String value, UiTypography.Role role, int color) {
        TextView view = new TextView(context);
        view.setText(value);
        view.setTextColor(color);
        UiTypography.apply(view, role);
        view.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
        return view;
    }

    /** Compatibility overload; maps old numeric requests onto the semantic scale. */
    TextView text(String value, int sp, int color, boolean emphasized) {
        return text(value, UiTypography.fromLegacySize(sp, emphasized), color);
    }

    TextView sectionTitle(String value) {
        TextView view = text(value, UiTypography.Role.CARD_TITLE, palette.text);
        view.setLetterSpacing(0.02f);
        view.setPadding(0, dp(24), 0, dp(8));
        if (Build.VERSION.SDK_INT >= 28) view.setAccessibilityHeading(true);
        view.setContentDescription(value + " section");
        return view;
    }

    LinearLayout surface() {
        LinearLayout layout = new LinearLayout(context);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(dp(16), dp(14), dp(16), dp(14));
        layout.setBackground(flat(palette.surface, 14));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, dp(6), 0, dp(6));
        layout.setLayoutParams(params);
        return layout;
    }

    LinearLayout tile(int iconRes, String title, String detail, String state,
                      int accent, String actionLabel, boolean actionEnabled,
                      View.OnClickListener listener) {
        LinearLayout tile = surface();
        tile.setPadding(dp(16), dp(16), dp(16), dp(14));

        LinearLayout top = new LinearLayout(context);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);

        ImageView icon = icon(iconRes, accent, 42);
        top.addView(icon, new LinearLayout.LayoutParams(dp(42), dp(42)));

        LinearLayout copy = new LinearLayout(context);
        copy.setOrientation(LinearLayout.VERTICAL);
        copy.setPadding(dp(12), 0, dp(8), 0);
        copy.addView(text(title, UiTypography.Role.CARD_TITLE, palette.text));
        TextView detailView = text(detail, UiTypography.Role.BODY_SMALL, palette.muted);
        detailView.setPadding(0, dp(3), 0, 0);
        copy.addView(detailView);
        top.addView(copy, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        TextView badge = text(state.toUpperCase(java.util.Locale.ROOT), UiTypography.Role.CAPTION, accent);
        badge.setLetterSpacing(0.04f);
        badge.setPadding(dp(8), dp(5), dp(8), dp(5));
        badge.setBackground(flat(withAlpha(accent, 28), 999));
        top.addView(badge);
        tile.addView(top);

        tile.setContentDescription(title + ". " + detail + ". Status: " + state + ".");
        tile.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
        Button action = button(actionLabel, withAlpha(accent, 32), listener);
        action.setContentDescription(actionLabel + " for " + title);
        action.setTextColor(accent);
        action.setEnabled(actionEnabled);
        action.setAlpha(actionEnabled ? 1f : 0.55f);
        tile.addView(action);
        return tile;
    }

    Button navButton(int iconRes, String label, boolean selected, View.OnClickListener listener) {
        Button button = new Button(context);
        button.setText(label);
        UiTypography.apply(button, UiTypography.Role.CAPTION);
        button.setAllCaps(false);
        button.setGravity(Gravity.CENTER);
        button.setTextColor(selected ? palette.green : palette.muted);
        button.setBackground(flat(selected ? withAlpha(palette.green, 22) : palette.background, 16));
        button.setMinHeight(dp(56));
        button.setMinimumHeight(dp(56));
        button.setPadding(dp(6), dp(7), dp(6), dp(6));
        button.setCompoundDrawablesWithIntrinsicBounds(0, iconRes, 0, 0);
        button.setCompoundDrawableTintList(ColorStateList.valueOf(selected ? palette.green : palette.muted));
        button.setCompoundDrawablePadding(dp(4));
        button.setContentDescription(label + (selected ? ", current page" : ", navigation tab"));
        button.setOnClickListener(listener);
        return button;
    }

    Button quickTile(int iconRes, String title, String detail, int accent, View.OnClickListener listener) {
        Button button = new Button(context);
        button.setText(title + "\n" + detail);
        UiTypography.apply(button, UiTypography.Role.BODY);
        button.setTextColor(palette.text);
        button.setAllCaps(false);
        button.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
        button.setPadding(dp(16), dp(14), dp(16), dp(14));
        button.setMinHeight(dp(78));
        button.setMinimumHeight(dp(48));
        button.setContentDescription(title + ". " + detail);
        button.setBackground(flat(palette.surface, 18));
        button.setCompoundDrawablesRelativeWithIntrinsicBounds(iconRes, 0, 0, 0);
        button.setCompoundDrawableTintList(ColorStateList.valueOf(accent));
        button.setCompoundDrawablePadding(dp(14));
        button.setOnClickListener(listener);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, dp(5), 0, dp(5));
        button.setLayoutParams(params);
        return button;
    }

    Button button(String label, int accent, View.OnClickListener listener) {
        Button button = new Button(context);
        button.setText(label);
        UiTypography.apply(button, UiTypography.Role.BUTTON);
        button.setTextColor(palette.background);
        button.setAllCaps(false);
        button.setGravity(Gravity.CENTER);
        button.setPadding(dp(14), dp(11), dp(14), dp(11));
        button.setMinHeight(dp(48));
        button.setMinimumHeight(dp(48));
        button.setContentDescription(label);
        button.setBackground(flat(accent, 11));
        button.setOnClickListener(listener);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, dp(8), 0, 0);
        button.setLayoutParams(params);
        return button;
    }

    Button secondaryButton(String label, View.OnClickListener listener) {
        Button button = button(label, palette.surfaceRaised, listener);
        button.setTextColor(palette.text);
        return button;
    }

    Switch toggle(String label, boolean checked) {
        Switch value = new Switch(context);
        value.setText(label);
        UiTypography.apply(value, UiTypography.Role.BODY);
        value.setTextColor(palette.text);
        value.setChecked(checked);
        value.setThumbTintList(new ColorStateList(
                new int[][]{new int[]{android.R.attr.state_checked}, new int[]{}},
                new int[]{palette.green, palette.muted}));
        value.setTrackTintList(new ColorStateList(
                new int[][]{new int[]{android.R.attr.state_checked}, new int[]{}},
                new int[]{withAlpha(palette.green, 110), withAlpha(palette.muted, 72)}));
        value.setPadding(0, dp(5), 0, dp(5));
        value.setMinHeight(dp(48));
        value.setMinimumHeight(dp(48));
        value.setContentDescription(label);
        return value;
    }

    EditText input(String value, String hint, boolean multiline) {
        EditText input = new EditText(context);
        input.setText(value);
        input.setHint(hint);
        input.setHintTextColor(palette.muted);
        input.setTextColor(palette.text);
        UiTypography.apply(input, multiline ? UiTypography.Role.BODY : UiTypography.Role.BODY_SMALL);
        input.setSingleLine(!multiline);
        input.setMinLines(multiline ? 3 : 1);
        input.setGravity(multiline ? Gravity.TOP : Gravity.CENTER_VERTICAL);
        input.setPadding(dp(12), dp(10), dp(12), dp(10));
        input.setMinHeight(dp(48));
        input.setMinimumHeight(dp(48));
        input.setId(View.generateViewId());
        input.setContentDescription(hint);
        input.setBackground(flat(palette.surfaceRaised, 10));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, dp(8), 0, 0);
        input.setLayoutParams(params);
        return input;
    }

    ImageView icon(int iconRes, int tint, int sizeDp) {
        ImageView icon = new ImageView(context);
        icon.setImageResource(iconRes);
        icon.setImageTintList(ColorStateList.valueOf(tint));
        icon.setPadding(dp(10), dp(10), dp(10), dp(10));
        icon.setBackground(flat(withAlpha(tint, 32), Math.max(8, sizeDp / 3)));
        icon.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        return icon;
    }

    <T extends View> T describe(T view, String description) {
        view.setContentDescription(description == null ? "" : description);
        view.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
        return view;
    }

    void labelInput(TextView label, EditText input, String name, String description) {
        if (input.getId() == View.NO_ID) input.setId(View.generateViewId());
        label.setLabelFor(input.getId());
        label.setContentDescription(name);
        input.setContentDescription(description == null || description.trim().isEmpty() ? name : name + ". " + description);
    }

    static int withAlpha(int color, int alpha) {
        return (color & 0x00FFFFFF) | ((alpha & 0xFF) << 24);
    }

    GradientDrawable flat(int fill, int radiusDp) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(fill);
        drawable.setCornerRadius(dp(radiusDp));
        return drawable;
    }
}
