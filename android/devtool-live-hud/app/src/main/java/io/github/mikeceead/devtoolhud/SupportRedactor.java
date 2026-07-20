package io.github.mikeceead.devtoolhud;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class SupportRedactor {
    private static final Pattern HTTPS_CREDENTIALS = Pattern.compile("(?i)(https?://)([^/@\\s]+)@*");
    private static final Pattern BEARER = Pattern.compile("(?i)(authorization\\s*[:=]\\s*)(bearer|basic)\\s+[^\\s\\\"']+");
    private static final Pattern KEY_VALUE_SECRET = Pattern.compile(
            "(?i)\\b(token|password|passwd|secret|credential|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key)\\b\\s*[:=]\\s*([^\\s,;]+)");
    private static final Pattern PRIVATE_KEY_BLOCK = Pattern.compile(
            "-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\\s\\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----");

    private SupportRedactor() {}

    static String redact(String value, String bridgeToken) {
        if (value == null || value.isEmpty()) return "";
        String clean = value;
        if (bridgeToken != null && !bridgeToken.trim().isEmpty()) {
            clean = clean.replace(bridgeToken.trim(), "<redacted:bridge-token>");
        }
        clean = PRIVATE_KEY_BLOCK.matcher(clean).replaceAll("<redacted:private-key>");
        clean = BEARER.matcher(clean).replaceAll("$1<redacted:auth-header>");
        clean = redactUrlCredentials(clean);
        clean = redactKeyValues(clean);
        return clean;
    }

    static String redact(String value) {
        return redact(value, "");
    }

    private static String redactKeyValues(String value) {
        Matcher matcher = KEY_VALUE_SECRET.matcher(value);
        StringBuffer output = new StringBuffer();
        while (matcher.find()) {
            matcher.appendReplacement(output, Matcher.quoteReplacement(matcher.group(1) + "=<redacted>"));
        }
        matcher.appendTail(output);
        return output.toString();
    }

    private static String redactUrlCredentials(String value) {
        Matcher matcher = HTTPS_CREDENTIALS.matcher(value);
        StringBuffer output = new StringBuffer();
        while (matcher.find()) {
            String credentials = matcher.group(2);
            if (credentials == null || credentials.isEmpty() || !credentials.contains(":")) {
                matcher.appendReplacement(output, Matcher.quoteReplacement(matcher.group(0)));
            } else {
                matcher.appendReplacement(output, Matcher.quoteReplacement(matcher.group(1) + "<redacted>@"));
            }
        }
        matcher.appendTail(output);
        return output.toString();
    }
}
