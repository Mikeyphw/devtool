package io.github.mikeceead.devtoolhud;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

final class RepositoryLocation {
    static final String TERMUX_HOME = "/data/data/com.termux/files/home";

    final String environment;
    final String chrootName;
    final String linuxUser;
    final String path;
    final String hostPath;

    RepositoryLocation(String environment, String chrootName, String linuxUser,
                       String path, String hostPath) {
        this.environment = cleanEnvironment(environment);
        this.chrootName = clean(chrootName);
        this.path = canonicalGuestPath(path);
        this.linuxUser = clean(linuxUser).isEmpty() ? inferUser(this.path) : clean(linuxUser);
        this.hostPath = canonicalHostPath(hostPath, this.environment, this.path);
    }

    static RepositoryLocation termux(String path) {
        String guest = normalizeTermuxPath(path);
        return new RepositoryLocation("native-termux", "", "termux", guest, toTermuxHostPath(guest));
    }

    static RepositoryLocation chroot(String name, String user, String path) {
        return new RepositoryLocation("chroot", name, user, path, "");
    }

    static RepositoryLocation root(String path) {
        return new RepositoryLocation("root", "", "root", path, path);
    }

    static RepositoryLocation fromRun(RunRecord run) {
        String environment = cleanEnvironment(run.executionEnvironment);
        String commandRepo = extractOption(run.command, "-r");
        if (commandRepo.isEmpty()) commandRepo = extractOption(run.command, "--repo");
        String repo = !commandRepo.isEmpty() ? commandRepo
                : (clean(run.repo).isEmpty() ? clean(run.workdir) : clean(run.repo));
        if (repo.isEmpty()) repo = "~/";
        String chroot = extractOption(run.command, "--chroot");
        if (environment.equals("chroot") || !chroot.isEmpty()
                || (repo.startsWith("/home/") && run.command.contains("devtool-androidos"))) {
            if (chroot.isEmpty()) chroot = "androidos";
            return chroot(chroot, inferUser(repo), repo);
        }
        if (repo.startsWith(TERMUX_HOME) || repo.startsWith("~/") || repo.equals("~")
                || repo.startsWith("$HOME")) return termux(repo);
        if (environment.equals("native-termux") || environment.equals("android-device")
                || environment.equals("auto")) return termux(repo);
        return root(repo);
    }

    String id() {
        return sha256(environment + "\u001f" + chrootName + "\u001f" + linuxUser
                + "\u001f" + path);
    }

    String displayEnvironment() {
        if (environment.equals("chroot")) return chrootName.isEmpty() ? "Chroot" : chrootName;
        if (environment.equals("native-termux")) return "Termux";
        return "Root";
    }

    private static String cleanEnvironment(String value) {
        String clean = clean(value).toLowerCase(Locale.ROOT);
        if (clean.equals("termux") || clean.equals("native") || clean.equals("auto"))
            return "native-termux";
        if (clean.equals("android-device")) return "root";
        if (clean.contains("chroot")) return "chroot";
        if (clean.isEmpty()) return "native-termux";
        return clean;
    }

    private static String canonicalGuestPath(String value) {
        String clean = clean(value).replace('\\', '/');
        if (clean.isEmpty()) return "/";
        while (clean.contains("//")) clean = clean.replace("//", "/");
        if (clean.length() > 1 && clean.endsWith("/")) clean = clean.substring(0, clean.length() - 1);
        return clean;
    }

    private static String canonicalHostPath(String hostPath, String environment, String guestPath) {
        String clean = canonicalGuestPath(hostPath);
        if (!clean.equals("/")) return clean;
        if (environment.equals("native-termux")) return toTermuxHostPath(guestPath);
        if (environment.equals("root")) return guestPath;
        return "";
    }

    static String normalizeTermuxPath(String value) {
        String clean = clean(value);
        if (clean.equals("~") || clean.equals("$HOME")) return "~/";
        if (clean.startsWith(TERMUX_HOME)) {
            String suffix = clean.substring(TERMUX_HOME.length());
            return suffix.isEmpty() ? "~/" : "~" + suffix;
        }
        if (clean.startsWith("${HOME}/")) return "~/" + clean.substring(8);
        if (clean.startsWith("$HOME/")) return "~/" + clean.substring(6);
        return canonicalGuestPath(clean);
    }

    static String toTermuxHostPath(String guestPath) {
        String clean = canonicalGuestPath(guestPath);
        if (clean.equals("~") || clean.equals("~/")) return TERMUX_HOME;
        if (clean.startsWith("~/")) return TERMUX_HOME + "/" + clean.substring(2);
        if (clean.startsWith("$HOME/")) return TERMUX_HOME + "/" + clean.substring(6);
        return clean;
    }

    static String inferUser(String path) {
        String clean = canonicalGuestPath(path);
        if (clean.startsWith("/home/")) {
            String rest = clean.substring(6);
            int slash = rest.indexOf('/');
            return slash < 0 ? rest : rest.substring(0, slash);
        }
        if (clean.startsWith("/root")) return "root";
        if (clean.startsWith("~/") || clean.equals("~")) return "termux";
        return "root";
    }

    private static String extractOption(String command, String option) {
        List<String> parts = shellTokens(command);
        String prefix = option + "=";
        for (int i = 0; i < parts.size(); i++) {
            String part = parts.get(i);
            if (part.startsWith(prefix)) return clean(part.substring(prefix.length()));
            if (part.equals(option) && i + 1 < parts.size()) return clean(parts.get(i + 1));
        }
        return "";
    }

    private static List<String> shellTokens(String command) {
        List<String> tokens = new ArrayList<>();
        String clean = clean(command);
        StringBuilder token = new StringBuilder();
        boolean single = false;
        boolean doubled = false;
        boolean escaped = false;
        for (int i = 0; i < clean.length(); i++) {
            char value = clean.charAt(i);
            if (escaped) {
                token.append(value);
                escaped = false;
                continue;
            }
            if (value == '\\' && !single) {
                escaped = true;
                continue;
            }
            if (value == '\'' && !doubled) {
                single = !single;
                continue;
            }
            if (value == '"' && !single) {
                doubled = !doubled;
                continue;
            }
            if (Character.isWhitespace(value) && !single && !doubled) {
                if (token.length() > 0) {
                    tokens.add(token.toString());
                    token.setLength(0);
                }
                continue;
            }
            token.append(value);
        }
        if (escaped) token.append('\\');
        if (token.length() > 0) tokens.add(token.toString());
        return tokens;
    }

    private static String stripQuotes(String value) {
        String clean = clean(value);
        if (clean.length() >= 2 && ((clean.startsWith("'") && clean.endsWith("'"))
                || (clean.startsWith("\"") && clean.endsWith("\"")))) {
            return clean.substring(1, clean.length() - 1);
        }
        return clean;
    }

    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder output = new StringBuilder();
            for (byte item : digest) output.append(String.format(Locale.ROOT, "%02x", item));
            return output.toString();
        } catch (Exception ignored) {
            return Integer.toHexString(value.hashCode());
        }
    }

    static String clean(String value) { return value == null ? "" : value.trim(); }
}
