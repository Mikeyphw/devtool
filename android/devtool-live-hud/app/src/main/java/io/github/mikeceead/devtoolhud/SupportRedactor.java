package io.github.mikeceead.devtoolhud;

import java.util.Locale;

final class SupportRedactor {
    private static final int MAX_RECORD_CHARS = 2 * 1024 * 1024;
    private static final int MAX_SECRET_CHARS = 64 * 1024;
    private static final String[] SECRET_WORDS = {
            "TOKEN", "PASSWORD", "PASSWD", "SECRET", "CREDENTIAL", "API_KEY", "AUTH", "PRIVATE_KEY"
    };

    private SupportRedactor() {}

    static String redact(String value, String bridgeToken) {
        if (value == null || value.isEmpty()) return "";
        String clean = value;
        if (bridgeToken != null && !bridgeToken.trim().isEmpty()) {
            clean = clean.replace(bridgeToken.trim(), "<redacted:bridge-token>");
        }
        clean = redactPrivateKeyBlocks(clean);
        return redactRecords(clean);
    }

    static String redact(String value) {
        return redact(value, "");
    }

    private static String redactPrivateKeyBlocks(String value) {
        String marker = "-----BEGIN ";
        StringBuilder output = new StringBuilder(value.length());
        int cursor = 0;
        while (cursor < value.length()) {
            int begin = value.indexOf(marker, cursor);
            if (begin < 0) {
                output.append(value, cursor, value.length());
                break;
            }
            int headerEnd = value.indexOf('\n', begin);
            if (headerEnd < 0) headerEnd = value.length();
            String header = value.substring(begin, headerEnd).toUpperCase(Locale.ROOT);
            if (!header.contains("PRIVATE KEY-----")) {
                int next = begin + marker.length();
                output.append(value, cursor, next);
                cursor = next;
                continue;
            }
            output.append(value, cursor, begin).append("<redacted:private-key>");
            int endBegin = value.indexOf("-----END ", headerEnd);
            if (endBegin < 0) break;
            int endLine = value.indexOf('\n', endBegin);
            endLine = endLine < 0 ? value.length() : endLine + 1;
            String footer = value.substring(endBegin, endLine).toUpperCase(Locale.ROOT);
            if (!footer.contains("PRIVATE KEY-----")) break;
            cursor = endLine;
        }
        return output.toString();
    }

    private static String redactRecords(String value) {
        StringBuilder output = new StringBuilder(value.length());
        int offset = 0;
        while (offset < value.length()) {
            int newline = value.indexOf('\n', offset);
            int end = newline < 0 ? value.length() : newline + 1;
            int length = end - offset;
            if (length > MAX_RECORD_CHARS) {
                output.append("<oversized-log-record-redacted:").append(length).append('>');
                if (newline >= 0) output.append('\n');
            } else {
                output.append(redactRecord(value.substring(offset, end)));
            }
            offset = end;
        }
        return output.toString();
    }

    private static String redactRecord(String record) {
        StringBuilder output = new StringBuilder(record.length());
        int literalStart = 0;
        int index = 0;
        while (index < record.length()) {
            char ch = record.charAt(index);

            if ((ch == 'A' || ch == 'a') && startsWithIgnoreCase(record, index, "authorization")) {
                int after = index + "authorization".length();
                boolean beforeOk = index == 0 || !isIdentifier(record.charAt(index - 1));
                boolean afterOk = after >= record.length() || !isIdentifier(record.charAt(after));
                if (beforeOk && afterOk) {
                    int cursor = skipSpaces(record, after);
                    if (cursor < record.length() && (record.charAt(cursor) == ':' || record.charAt(cursor) == '=')) {
                        cursor = skipSpaces(record, cursor + 1);
                        String scheme = null;
                        if (startsWithIgnoreCase(record, cursor, "bearer")) scheme = "bearer";
                        else if (startsWithIgnoreCase(record, cursor, "basic")) scheme = "basic";
                        if (scheme != null) {
                            int schemeEnd = cursor + scheme.length();
                            if (schemeEnd < record.length() && Character.isWhitespace(record.charAt(schemeEnd))) {
                                int valueStart = skipSpaces(record, schemeEnd);
                                if (valueStart < record.length()) {
                                    int valueEnd = secretValueEnd(record, valueStart);
                                    output.append(record, literalStart, valueStart).append("<redacted:auth-header>");
                                    literalStart = valueEnd;
                                    index = valueEnd;
                                    continue;
                                }
                            }
                        }
                    }
                }
            }

            if ((ch == 'h' || ch == 'H') && (startsWithIgnoreCase(record, index, "https://") || startsWithIgnoreCase(record, index, "http://"))) {
                int schemeLength = startsWithIgnoreCase(record, index, "https://") ? 8 : 7;
                int authStart = index + schemeLength;
                int cursor = authStart;
                int colon = -1;
                int at = -1;
                while (cursor < record.length()) {
                    char current = record.charAt(cursor);
                    if (current == '/' || Character.isWhitespace(current)) break;
                    if (current == ':' && colon < 0) colon = cursor;
                    else if (current == '@') { at = cursor; break; }
                    cursor++;
                }
                if (colon >= authStart && at > colon) {
                    output.append(record, literalStart, authStart).append("<redacted>@");
                    literalStart = at + 1;
                    index = at + 1;
                    continue;
                }
            }

            if (isIdentifier(ch) && (index == 0 || !isIdentifier(record.charAt(index - 1)))) {
                int nameEnd = index + 1;
                while (nameEnd < record.length() && isIdentifier(record.charAt(nameEnd))) nameEnd++;
                int separator = skipSpaces(record, nameEnd);
                if (separator < record.length() && (record.charAt(separator) == '=' || record.charAt(separator) == ':')) {
                    int valueStart = skipSpaces(record, separator + 1);
                    if (valueStart < record.length() && isSecretIdentifier(record.substring(index, nameEnd))) {
                        int valueEnd = secretValueEnd(record, valueStart);
                        output.append(record, literalStart, valueStart).append("<redacted>");
                        literalStart = valueEnd;
                        index = valueEnd;
                        continue;
                    }
                }
                index = nameEnd;
                continue;
            }
            index++;
        }
        output.append(record, literalStart, record.length());
        return output.toString();
    }

    private static int secretValueEnd(String value, int start) {
        int limit = Math.min(value.length(), start + MAX_SECRET_CHARS);
        if (start >= value.length()) return start;
        char quote = value.charAt(start);
        if (quote == '\'' || quote == '"') {
            int index = start + 1;
            while (index < limit && value.charAt(index) != quote && value.charAt(index) != '\r' && value.charAt(index) != '\n') index++;
            if (index < value.length() && value.charAt(index) == quote) return index + 1;
            return value.length();
        }
        int index = start;
        while (index < limit) {
            char ch = value.charAt(index);
            if (Character.isWhitespace(ch) || ch == ';' || ch == ',') break;
            index++;
        }
        return index < limit ? index : value.length();
    }

    private static int skipSpaces(String value, int index) {
        while (index < value.length() && (value.charAt(index) == ' ' || value.charAt(index) == '\t')) index++;
        return index;
    }

    private static boolean isIdentifier(char ch) {
        return Character.isLetterOrDigit(ch) || ch == '_' || ch == '-';
    }

    private static boolean isSecretIdentifier(String name) {
        String normalized = name.toUpperCase(Locale.ROOT).replace('-', '_');
        for (String word : SECRET_WORDS) {
            if (normalized.contains(word)) return true;
        }
        return false;
    }

    private static boolean startsWithIgnoreCase(String value, int offset, String expected) {
        return offset >= 0 && offset + expected.length() <= value.length()
                && value.regionMatches(true, offset, expected, 0, expected.length());
    }
}
