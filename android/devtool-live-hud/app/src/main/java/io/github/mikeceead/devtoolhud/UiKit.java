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

    LinearLayout tile(String symbol, String title, String detail, String state,
                      int accent, String actionLabel, boolean actionEnabled,
                      View.OnClickListener listener) {
        LinearLayout tile = surface();
        tile.setPadding(dp(16), dp(16), dp(16), dp(14));

        LinearLayout top = new LinearLayout(context);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);

        TextView icon = text(symbol, 18, accent, true);
        icon.setGravity(Gravity.CENTER);
        icon.setBackground(flat(withAlpha(accent, 32), 13));
        top.addView(icon, new LinearLayout.LayoutParams(dp(42), dp(42)));

        LinearLayout copy = new LinearLayout(context);
        copy.setOrientation(LinearLayout.VERTICAL);
        copy.setPadding(dp(12), 0, dp(8), 0);
        copy.addView(text(title, 15, palette.text, true));
        TextView detailView = text(detail, 12, palette.muted, false);
        detailView.setPadding(0, dp(3), 0, 0);
        copy.addView(detailView);
        top.addView(copy, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        TextView badge = text(state.toUpperCase(), 10, accent, true);
        badge.setLetterSpacing(0.08f);
        badge.setPadding(dp(8), dp(5), dp(8), dp(5));
        badge.setBackground(flat(withAlpha(accent, 28), 999));
        top.addView(badge);
        tile.addView(top);

        Button action = button(actionLabel, withAlpha(accent, 32), listener);
        action.setTextColor(accent);
        action.setEnabled(actionEnabled);
        action.setAlpha(actionEnabled ? 1f : 0.55f);
        tile.addView(action);
        return tile;
    }

    Button navButton(String symbol, String label, boolean selected, View.OnClickListener listener) {
        Button button = new Button(context);
        button.setText(symbol + "\n" + label);
        button.setTextSize(11);
        button.setAllCaps(false);
        button.setTypeface(Typeface.DEFAULT, selected ? Typeface.BOLD : Typeface.NORMAL);
        button.setGravity(Gravity.CENTER);
        button.setTextColor(selected ? palette.green : palette.muted);
        button.setBackground(flat(selected ? withAlpha(palette.green, 22) : palette.background, 16));
        button.setMinHeight(0);
        button.setMinimumHeight(0);
        button.setPadding(dp(6), dp(7), dp(6), dp(6));
        button.setOnClickListener(listener);
        return button;
    }

    Button quickTile(String symbol, String title, String detail, int accent, View.OnClickListener listener) {
        Button button = new Button(context);
        button.setText(symbol + "   " + title + "\n" + detail);
        button.setTextSize(13);
        button.setTextColor(palette.text);
        button.setAllCaps(false);
        button.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        button.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
        button.setPadding(dp(16), dp(14), dp(16), dp(14));
        button.setMinHeight(dp(74));
        button.setBackground(flat(palette.surface, 18));
        button.setCompoundDrawableTintList(ColorStateList.valueOf(accent));
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
