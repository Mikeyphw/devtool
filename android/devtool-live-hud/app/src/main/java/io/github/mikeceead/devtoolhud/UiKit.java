package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
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

    TextView text(String value, int sp, int color, boolean bold) {
        TextView view = new TextView(context);
        view.setText(value);
        view.setTextSize(sp);
        view.setTextColor(color);
        view.setTypeface(Typeface.DEFAULT, bold ? Typeface.BOLD : Typeface.NORMAL);
        view.setLineSpacing(0, 1.12f);
        return view;
    }

    TextView sectionTitle(String value) {
        TextView view = text(value, 13, palette.cyan, true);
        view.setAllCaps(true);
        view.setLetterSpacing(0.08f);
        view.setPadding(0, dp(24), 0, dp(8));
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

    Button button(String label, int accent, View.OnClickListener listener) {
        Button button = new Button(context);
        button.setText(label);
        button.setTextSize(14);
        button.setTextColor(palette.background);
        button.setAllCaps(false);
        button.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        button.setGravity(Gravity.CENTER);
        button.setPadding(dp(14), dp(11), dp(14), dp(11));
        button.setMinHeight(0);
        button.setMinimumHeight(0);
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
        value.setTextSize(15);
        value.setTextColor(palette.text);
        value.setChecked(checked);
        value.setThumbTintList(new ColorStateList(
                new int[][]{new int[]{android.R.attr.state_checked}, new int[]{}},
                new int[]{palette.green, palette.muted}));
        value.setTrackTintList(new ColorStateList(
                new int[][]{new int[]{android.R.attr.state_checked}, new int[]{}},
                new int[]{withAlpha(palette.green, 110), withAlpha(palette.muted, 72)}));
        value.setPadding(0, dp(5), 0, dp(5));
        return value;
    }

    EditText input(String value, String hint, boolean multiline) {
        EditText input = new EditText(context);
        input.setText(value);
        input.setHint(hint);
        input.setHintTextColor(palette.muted);
        input.setTextColor(palette.text);
        input.setTextSize(14);
        input.setSingleLine(!multiline);
        input.setMinLines(multiline ? 3 : 1);
        input.setGravity(multiline ? Gravity.TOP : Gravity.CENTER_VERTICAL);
        input.setPadding(dp(12), dp(10), dp(12), dp(10));
        input.setBackground(flat(palette.surfaceRaised, 10));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, dp(8), 0, 0);
        input.setLayoutParams(params);
        return input;
    }

    private static int withAlpha(int color, int alpha) {
        return (color & 0x00FFFFFF) | ((alpha & 0xFF) << 24);
    }

    GradientDrawable flat(int fill, int radiusDp) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(fill);
        drawable.setCornerRadius(dp(radiusDp));
        return drawable;
    }
}
