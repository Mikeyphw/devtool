package io.github.mikeceead.devtoolhud;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

final class GitCommitRecord {
    String fullHash = "";
    String shortHash = "";
    String parents = "";
    String authorName = "";
    String authorEmail = "";
    String authoredAt = "";
    String refs = "";
    String subject = "";
    boolean head;

    static List<GitCommitRecord> parse(byte[] bytes) {
        List<String> fields = splitNull(bytes == null ? new byte[0] : bytes);
        List<GitCommitRecord> values = new ArrayList<>();
        if (fields.isEmpty()) return values;
        String head = fields.get(0);
        for (int index = 1; index + 7 < fields.size(); index += 8) {
            GitCommitRecord commit = new GitCommitRecord();
            commit.fullHash = fields.get(index);
            commit.shortHash = fields.get(index + 1);
            commit.parents = fields.get(index + 2);
            commit.authorName = fields.get(index + 3);
            commit.authorEmail = fields.get(index + 4);
            commit.authoredAt = fields.get(index + 5);
            commit.refs = fields.get(index + 6);
            commit.subject = fields.get(index + 7);
            commit.head = !head.isEmpty() && head.equals(commit.fullHash);
            values.add(commit);
        }
        return values;
    }

    private static List<String> splitNull(byte[] bytes) {
        List<String> values = new ArrayList<>();
        int start = 0;
        for (int index = 0; index <= bytes.length; index++) {
            if (index == bytes.length || bytes[index] == 0) {
                values.add(index > start
                        ? new String(bytes, start, index - start, StandardCharsets.UTF_8) : "");
                start = index + 1;
            }
        }
        if (!values.isEmpty() && values.get(values.size() - 1).isEmpty()) values.remove(values.size() - 1);
        return values;
    }
}
