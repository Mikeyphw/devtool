package io.github.mikeceead.devtoolhud;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class TestCatalog {
    private static final Pattern ANSI = Pattern.compile("\\x1B\\[[0-9;]*[A-Za-z]");
    private static final Pattern JSON_ID = Pattern.compile("\\\"(?:nodeid|id|test|name)\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");
    private static final Pattern TESTISH = Pattern.compile("(?m)^\\s*[-*+•]?\\s*([A-Za-z0-9_./\\-]+(?:::[A-Za-z0-9_.$<>\\[\\]\\-]+)+)\\s*$");

    static final class Item {
        final String id;
        final String searchKey;
        Item(String id) {
            this.id = id;
            this.searchKey = id.toLowerCase(Locale.ROOT);
        }
    }

    private TestCatalog() {}

    static List<Item> fromRuns(List<RunRecord> runs) {
        Set<String> ids = new LinkedHashSet<>();
        for (RunRecord run : runs) {
            scan(ids, run.stdout);
            scan(ids, run.stderr);
            scan(ids, run.summary);
            scan(ids, run.detail);
        }
        List<Item> result = new ArrayList<>();
        for (String id : ids) result.add(new Item(id));
        return result;
    }

    static List<Item> filter(List<Item> source, String query) {
        String needle = query == null ? "" : query.trim().toLowerCase(Locale.ROOT);
        if (needle.isEmpty()) return new ArrayList<>(source);
        List<Item> result = new ArrayList<>();
        for (Item item : source) if (item.searchKey.contains(needle)) result.add(item);
        return result;
    }

    private static void scan(Set<String> ids, String text) {
        if (text == null || text.isEmpty()) return;
        Matcher json = JSON_ID.matcher(text);
        while (json.find()) add(ids, json.group(1));
        String plain = ANSI.matcher(text).replaceAll("");
        Matcher test = TESTISH.matcher(plain);
        while (test.find()) add(ids, test.group(1));
    }

    private static void add(Set<String> ids, String value) {
        String clean = value == null ? "" : value.trim().replace("\\n", "\n");
        if (!clean.isEmpty() && clean.length() < 600) ids.add(clean);
    }

}
