package io.github.mikeceead.devtoolhud;

// import android.annotation.SuppressLint;
/** Path normalization for Termux RUN_COMMAND, which requires an absolute work directory. */
// @SuppressLint("SdCardPath")
final class TermuxPaths {
    static final String HOME = "/data/data/com.termux/files/home";

    private TermuxPaths() {}

    static String normalizeWorkdir(String workdir) {
        String value = workdir == null ? "" : workdir.trim();
        if (value.isEmpty() || value.equals("~") || value.equals("~/")
                || value.equals("$HOME") || value.equals("${HOME}")) {
            return HOME;
        }

        // Recover values persisted by older app versions that prefixed a slash
        // before an unexpanded shell home expression.
        if (value.startsWith("/$HOME/")) value = value.substring(1);
        if (value.startsWith("/${HOME}/")) value = value.substring(1);
        if (value.startsWith("/~")) value = value.substring(1);

        if (value.startsWith("~/")) {
            return HOME + value.substring(1);
        }
        if (value.startsWith("$HOME/")) {
            return HOME + value.substring("$HOME".length());
        }
        if (value.startsWith("${HOME}/")) {
            return HOME + value.substring("${HOME}".length());
        }
        if (value.startsWith("/")) {
            return value;
        }

        // Treat a relative directory as relative to the Termux home directory.
        return HOME + "/" + value;
    }
}
