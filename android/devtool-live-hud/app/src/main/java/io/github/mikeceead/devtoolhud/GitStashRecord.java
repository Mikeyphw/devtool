package io.github.mikeceead.devtoolhud;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

final class GitStashRecord {
    String reference = "";
    String objectId = "";
    String createdAt = "";
    String subject = "";

    static List<GitStashRecord> parse(byte[] bytes) {
        List<String> fields = splitNull(bytes == null ? new byte[0] : bytes);
        List<GitStashRecord> values = new ArrayList<>();
        for (int index = 0; index + 3 < fields.size(); index += 4) {
            GitStashRecord stash = new GitStashRecord();
            stash.reference = clean(fields.get(index));
            stash.objectId = clean(fields.get(index + 1));
            stash.createdAt = clean(fields.get(index + 2));
            stash.subject = clean(fields.get(index + 3));
            if (stash.reference.matches("stash@\\{\\d+}")) values.add(stash);
        }
        return values;
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }

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
}
