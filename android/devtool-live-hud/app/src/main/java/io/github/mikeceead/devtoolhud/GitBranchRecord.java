package io.github.mikeceead.devtoolhud;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

final class GitBranchRecord {
    String fullRef = "";
    String name = "";
    String objectId = "";
    String upstream = "";
    boolean current;
    boolean remote;
    boolean upstreamGone;
    int ahead;
    int behind;

    String trackingLabel() {
        if (upstreamGone) return "upstream gone";
        if (ahead == 0 && behind == 0) return upstream;
        StringBuilder value = new StringBuilder();
        if (ahead > 0) value.append(ahead).append(" ahead");
        if (behind > 0) {
            if (value.length() > 0) value.append(" · ");
            value.append(behind).append(" behind");
        }
        return value.toString();
    }

    static List<GitBranchRecord> parse(byte[] bytes) {
        List<String> fields = splitNull(bytes == null ? new byte[0] : bytes);
        List<GitBranchRecord> values = new ArrayList<>();
        for (int index = 0; index + 6 < fields.size(); index += 7) {
            GitBranchRecord branch = new GitBranchRecord();
            branch.fullRef = clean(fields.get(index));
            branch.name = clean(fields.get(index + 1));
            branch.objectId = clean(fields.get(index + 2));
            branch.upstream = clean(fields.get(index + 3));
            parseTrack(branch, fields.get(index + 4));
            branch.current = fields.get(index + 5).equals("*");
            branch.remote = fields.get(index + 6).equals("remote");
            if (branch.name.isEmpty() || branch.name.endsWith("/HEAD")) continue;
            values.add(branch);
        }
        return values;
    }

    private static void parseTrack(GitBranchRecord branch, String raw) {
        String track = raw == null ? "" : raw.trim().toLowerCase(Locale.ROOT);
        if (track.contains("gone")) branch.upstreamGone = true;
        java.util.regex.Matcher aheadMatcher = java.util.regex.Pattern
                .compile("ahead\\s+(\\d+)").matcher(track);
        java.util.regex.Matcher behindMatcher = java.util.regex.Pattern
                .compile("behind\\s+(\\d+)").matcher(track);
        if (aheadMatcher.find()) branch.ahead = parseInt(aheadMatcher.group(1));
        if (behindMatcher.find()) branch.behind = parseInt(behindMatcher.group(1));
    }

    private static List<String> splitNull(byte[] bytes) {
        List<String> values = new ArrayList<>();
        int start = 0;
        for (int index = 0; index <= bytes.length; index++) {
            if (index == bytes.length || bytes[index] == 0) {
                values.add(new String(bytes, start, index - start, StandardCharsets.UTF_8));
                start = index + 1;
            }
        }
        while (!values.isEmpty() && values.get(values.size() - 1).isEmpty()) {
            values.remove(values.size() - 1);
        }
        return values;
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }

    private static int parseInt(String value) {
        try { return Integer.parseInt(value); }
        catch (NumberFormatException ignored) { return 0; }
    }
}
