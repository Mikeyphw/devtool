package io.github.mikeceead.devtoolhud;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

final class GitWorkingTree {
    String headOid = "";
    String branch = "";
    String upstream = "";
    int ahead;
    int behind;
    boolean detached;
    String error = "";
    final List<GitChange> changes = new ArrayList<>();

    boolean success() { return error.isEmpty(); }
    boolean dirty() { return !changes.isEmpty(); }
    int stagedCount() {
        int count = 0;
        for (GitChange change : changes) if (change.staged()) count++;
        return count;
    }
    int unstagedCount() {
        int count = 0;
        for (GitChange change : changes) if (change.unstaged()) count++;
        return count;
    }
    int conflictedCount() {
        int count = 0;
        for (GitChange change : changes) if (change.conflicted()) count++;
        return count;
    }

    Map<String, List<GitChange>> grouped() {
        Map<String, List<GitChange>> values = new LinkedHashMap<>();
        for (String name : new String[]{"Conflicted", "Renamed", "Deleted", "Staged", "Modified", "Untracked"}) {
            values.put(name, new ArrayList<>());
        }
        for (GitChange change : changes) values.get(change.group()).add(change);
        for (List<GitChange> group : values.values()) {
            group.sort(Comparator.comparing(item -> item.path.toLowerCase(Locale.ROOT)));
        }
        values.entrySet().removeIf(entry -> entry.getValue().isEmpty());
        return values;
    }

    static GitWorkingTree parse(byte[] bytes, int exitCode, boolean timedOut) {
        GitWorkingTree tree = new GitWorkingTree();
        if (timedOut) {
            tree.error = "Git status timed out";
            return tree;
        }
        if (exitCode != 0) {
            String text = new String(bytes == null ? new byte[0] : bytes, StandardCharsets.UTF_8).trim();
            tree.error = text.isEmpty() ? "Git status failed" : compact(text);
            return tree;
        }
        List<String> tokens = splitNull(bytes == null ? new byte[0] : bytes);
        for (int index = 0; index < tokens.size(); index++) {
            String token = tokens.get(index);
            if (token.startsWith("# branch.oid ")) {
                tree.headOid = token.substring(13).trim();
                if (tree.headOid.equals("(initial)")) tree.headOid = "";
                continue;
            }
            if (token.startsWith("# branch.head ")) {
                tree.branch = token.substring(14).trim();
                tree.detached = tree.branch.equals("(detached)");
                continue;
            }
            if (token.startsWith("# branch.upstream ")) {
                tree.upstream = token.substring(18).trim();
                continue;
            }
            if (token.startsWith("# branch.ab ")) {
                java.util.regex.Matcher matcher = java.util.regex.Pattern
                        .compile("\\+(\\d+)\\s+-(\\d+)").matcher(token);
                if (matcher.find()) {
                    tree.ahead = parseInt(matcher.group(1));
                    tree.behind = parseInt(matcher.group(2));
                }
                continue;
            }
            if (token.startsWith("1 ")) {
                String[] fields = token.split(" ", 9);
                if (fields.length >= 9) tree.changes.add(GitChange.ordinary(fields[1], fields[2], fields[8]));
                continue;
            }
            if (token.startsWith("2 ")) {
                String[] fields = token.split(" ", 10);
                String original = index + 1 < tokens.size() ? tokens.get(++index) : "";
                if (fields.length >= 10) tree.changes.add(GitChange.renamed(fields[1], fields[2], fields[9], original));
                continue;
            }
            if (token.startsWith("u ")) {
                String[] fields = token.split(" ", 11);
                if (fields.length >= 11) tree.changes.add(GitChange.unmerged(fields[1], fields[2], fields[10]));
                continue;
            }
            if (token.startsWith("? ")) {
                tree.changes.add(GitChange.untracked(token.substring(2)));
            }
        }
        Collections.sort(tree.changes, Comparator.comparing(item -> item.path.toLowerCase(Locale.ROOT)));
        return tree;
    }

    private static List<String> splitNull(byte[] bytes) {
        List<String> values = new ArrayList<>();
        int start = 0;
        for (int index = 0; index <= bytes.length; index++) {
            if (index == bytes.length || bytes[index] == 0) {
                if (index > start) values.add(new String(bytes, start, index - start, StandardCharsets.UTF_8));
                start = index + 1;
            }
        }
        return values;
    }

    private static int parseInt(String value) {
        try { return Integer.parseInt(value); }
        catch (NumberFormatException ignored) { return 0; }
    }

    private static String compact(String value) {
        String clean = value.replace('\n', ' ').replace('\r', ' ').trim();
        return clean.length() > 400 ? clean.substring(0, 397) + "…" : clean;
    }

    static final class GitChange {
        final String recordType;
        final String xy;
        final String submodule;
        final String path;
        final String originalPath;

        private GitChange(String recordType, String xy, String submodule, String path, String originalPath) {
            this.recordType = clean(recordType);
            this.xy = clean(xy);
            this.submodule = clean(submodule);
            this.path = cleanPath(path);
            this.originalPath = cleanPath(originalPath);
        }

        static GitChange ordinary(String xy, String submodule, String path) {
            return new GitChange("1", xy, submodule, path, "");
        }
        static GitChange renamed(String xy, String submodule, String path, String originalPath) {
            return new GitChange("2", xy, submodule, path, originalPath);
        }
        static GitChange unmerged(String xy, String submodule, String path) {
            return new GitChange("u", xy, submodule, path, "");
        }
        static GitChange untracked(String path) {
            return new GitChange("?", "??", "N...", path, "");
        }

        char indexStatus() { return xy.length() > 0 ? xy.charAt(0) : '.'; }
        char worktreeStatus() { return xy.length() > 1 ? xy.charAt(1) : '.'; }
        boolean staged() { return recordType.equals("u") || (!recordType.equals("?") && changed(indexStatus())); }
        boolean unstaged() { return recordType.equals("?") || recordType.equals("u") || changed(worktreeStatus()); }
        boolean untracked() { return recordType.equals("?"); }
        boolean conflicted() {
            if (recordType.equals("u")) return true;
            String state = xy.toUpperCase(Locale.ROOT);
            return state.equals("DD") || state.equals("AU") || state.equals("UD")
                    || state.equals("UA") || state.equals("DU") || state.equals("AA") || state.equals("UU");
        }
        boolean renamed() { return recordType.equals("2") || indexStatus() == 'R' || worktreeStatus() == 'R'; }
        boolean deleted() { return indexStatus() == 'D' || worktreeStatus() == 'D'; }
        boolean canStage() { return untracked() || conflicted() || unstaged(); }
        boolean canUnstage() { return staged() && !conflicted(); }
        boolean canDiscardWorktree() { return !untracked() && !conflicted() && unstaged(); }
        boolean hasWorkingDiff() { return untracked() || unstaged(); }
        boolean hasStagedDiff() { return staged(); }

        String group() {
            if (conflicted()) return "Conflicted";
            if (renamed()) return "Renamed";
            if (deleted()) return "Deleted";
            if (untracked()) return "Untracked";
            if (staged()) return "Staged";
            return "Modified";
        }

        String statusLabel() {
            if (conflicted()) return "CONFLICT";
            if (untracked()) return "UNTRACKED";
            if (renamed()) return staged() && unstaged() ? "RENAMED · STAGED + MODIFIED" : staged() ? "RENAMED · STAGED" : "RENAMED";
            if (deleted()) return staged() && unstaged() ? "DELETED · STAGED + MODIFIED" : staged() ? "DELETED · STAGED" : "DELETED";
            if (staged() && unstaged()) return "STAGED + MODIFIED";
            if (staged()) return "STAGED";
            return "MODIFIED";
        }

        String displayPath() {
            return originalPath.isEmpty() ? path : originalPath + " → " + path;
        }

        private static boolean changed(char value) { return value != '.' && value != ' '; }
        private static String clean(String value) { return value == null ? "" : value.trim(); }
        private static String cleanPath(String value) {
            String clean = value == null ? "" : value;
            while (clean.startsWith("./")) clean = clean.substring(2);
            return clean;
        }
    }
}
