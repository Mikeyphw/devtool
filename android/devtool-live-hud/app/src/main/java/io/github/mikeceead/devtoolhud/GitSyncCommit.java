package io.github.mikeceead.devtoolhud;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

final class GitSyncCommit {
    String hash = "";
    String shortHash = "";
    String author = "";
    String authoredAt = "";
    String subject = "";

    static List<GitSyncCommit> parse(byte[] bytes) {
        List<GitSyncCommit> values = new ArrayList<>();
        if (bytes == null || bytes.length == 0) return values;
        String text = new String(bytes, StandardCharsets.UTF_8);
        String[] parts = text.split("\\u0000", -1);
        for (int index = 0; index + 4 < parts.length; index += 5) {
            if (parts[index].isEmpty()) break;
            GitSyncCommit value = new GitSyncCommit();
            value.hash = parts[index];
            value.shortHash = parts[index + 1];
            value.author = parts[index + 2];
            value.authoredAt = parts[index + 3];
            value.subject = parts[index + 4];
            values.add(value);
        }
        return values;
    }
}
