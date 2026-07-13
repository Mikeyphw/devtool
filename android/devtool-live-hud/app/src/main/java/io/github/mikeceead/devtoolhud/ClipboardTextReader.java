package io.github.mikeceead.devtoolhud;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.net.Uri;

/** Safe, single-read clipboard access for Android's evolving privacy model. */
final class ClipboardTextReader {
    static final class Result {
        final String text;
        final String error;

        private Result(String text, String error) {
            this.text = text;
            this.error = error;
        }

        static Result success(String text) { return new Result(text, ""); }
        static Result failure(String error) { return new Result("", error); }
        boolean ok() { return !text.isEmpty(); }
    }

    private ClipboardTextReader() {}

    static Result read(Context context) {
        ClipboardManager clipboard;
        try {
            clipboard = context.getSystemService(ClipboardManager.class);
        } catch (RuntimeException error) {
            return Result.failure("Clipboard is temporarily unavailable");
        }
        if (clipboard == null) return Result.failure("Clipboard is unavailable");

        final ClipData clip;
        try {
            // Read the primary clip exactly once. Repeated calls can race with
            // Android's clipboard privacy overlay and with clipboard providers.
            if (!clipboard.hasPrimaryClip()) return Result.failure("Clipboard is empty");
            clip = clipboard.getPrimaryClip();
        } catch (SecurityException error) {
            return Result.failure("Android blocked clipboard access. Keep the app visible and try again.");
        } catch (RuntimeException error) {
            return Result.failure("Clipboard could not be read. Try again.");
        }

        if (clip == null || clip.getItemCount() == 0) return Result.failure("Clipboard is empty");
        ClipData.Item item = clip.getItemAt(0);
        if (item == null) return Result.failure("Clipboard does not contain text");

        try {
            CharSequence direct = item.getText();
            if (direct != null) {
                String text = direct.toString().trim();
                if (!text.isEmpty()) return Result.success(text);
            }

            // coerceToText can dereference content URIs and throw on clipboard
            // providers that do not grant access. Keep that path guarded so a
            // first paste tap can never terminate the activity.
            Uri uri = item.getUri();
            if (uri != null) {
                CharSequence coerced = item.coerceToText(context);
                String text = coerced == null ? "" : coerced.toString().trim();
                if (!text.isEmpty()) return Result.success(text);
            }
        } catch (SecurityException error) {
            return Result.failure("Clipboard content is protected by another app");
        } catch (RuntimeException error) {
            return Result.failure("Clipboard content could not be converted to text");
        }
        return Result.failure("Clipboard does not contain text");
    }
}
