package io.github.mikeceead.devtoolhud;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

/** Tolerant importer for commands copied from terminals, documentation, or chat. */
final class DevtoolCommandImporter {
    private static final Pattern ASSIGNMENT = Pattern.compile("[A-Za-z_][A-Za-z0-9_]*=.*", Pattern.DOTALL);

    static final class ImportResult {
        final DevtoolRunConfig config;
        final String workdir;
        final String notice;

        ImportResult(DevtoolRunConfig config, String workdir, String notice) {
            this.config = config;
            this.workdir = workdir;
            this.notice = notice;
        }
    }

    private DevtoolCommandImporter() {}

    static ImportResult parse(String source) {
        List<String> tokens = tokenize(source);
        if (tokens.isEmpty()) throw new IllegalArgumentException("Clipboard does not contain a command");

        int executable = findExecutable(tokens);
        if (executable < 0) {
            throw new IllegalArgumentException("Clipboard does not contain a recognizable devtool command");
        }

        DevtoolRunConfig config = emptyConfig();
        String workdir = "";
        List<String> environment = new ArrayList<>();
        List<String> notices = new ArrayList<>();

        for (int i = 0; i < executable; i++) {
            String token = tokens.get(i);
            if (isPromptMarker(token) || token.equals("\\")) {
                continue;
            }
            if (token.equals("cd") && i + 1 < executable) {
                if (isConnector(tokens.get(i + 1))) {
                    throw new IllegalArgumentException("The pasted command has an incomplete cd prefix");
                }
                workdir = tokens.get(++i);
                if (i + 1 < executable && isConnector(tokens.get(i + 1))) i++;
            } else if (token.equals("env")) {
                // `env KEY=value devtool ...` is accepted without adding a literal env command.
            } else if (ASSIGNMENT.matcher(token).matches()) {
                environment.add(renderAssignment(token));
            } else if (!isConnector(token)) {
                throw new IllegalArgumentException("Clipboard does not contain a recognizable devtool command");
            }
        }
        config.environmentAssignments = String.join(" ", environment);

        String executableName = basename(tokens.get(executable));
        config.useChroot = executableName.equals("devtool-androidos");

        List<String> globalExtras = new ArrayList<>();
        int index = executable + 1;
        int knownOperationIndex = findKnownOperation(tokens, index);
        while (index < tokens.size()) {
            String token = tokens.get(index);
            if (isConnector(token)) {
                notices.add("Trailing shell command was not imported");
                break;
            }
            if (index == knownOperationIndex || DevtoolCommandBuilder.isOperation(token)) break;
            if (knownOperationIndex < 0 && !token.startsWith("-")) break;
            switch (token) {
                case "--chroot":
                    config.useChroot = true;
                    config.chrootName = requireValue(tokens, ++index, token);
                    index++;
                    continue;
                case "--copy":
                    config.copy = true;
                    index++;
                    continue;
                case "--no-copy":
                    config.copy = false;
                    index++;
                    continue;
                case "--copy-via":
                    String copyVia = requireValue(tokens, ++index, token);
                    config.copyViaTermux = copyVia.equalsIgnoreCase("termux");
                    if (!config.copyViaTermux) {
                        globalExtras.add("--copy-via");
                        globalExtras.add(renderToken(copyVia));
                    }
                    index++;
                    continue;
                case "--auto-hud":
                    config.autoHud = true;
                    index++;
                    continue;
                case "--no-hud":
                case "--no-auto-hud":
                    config.autoHud = false;
                    index++;
                    continue;
                case "--hud-mode":
                    String hudMode = requireValue(tokens, ++index, token);
                    config.autoHud = !hudMode.equals("none") && !hudMode.equals("off");
                    if (hudMode.equals("desktop-window") || hudMode.equals("android-gui") || hudMode.equals("termux-gui")) {
                        notices.add("HUD mode " + hudMode + " was converted to android-bridge");
                    } else if (!hudMode.equals("android-bridge")) {
                        notices.add("HUD mode " + hudMode + " was imported as Android live progress");
                    }
                    index++;
                    continue;
                case "--yes":
                case "-y":
                    config.assumeYes = true;
                    index++;
                    continue;
                case "-r":
                case "--repo":
                case "--repository":
                    config.repo = requireValue(tokens, ++index, token);
                    index++;
                    continue;
                case "--target":
                    config.target = requireValue(tokens, ++index, token);
                    index++;
                    continue;
                case "--":
                    index++;
                    continue;
                default:
                    globalExtras.add(renderToken(token));
                    index++;
            }
        }

        if (index >= tokens.size() || isConnector(tokens.get(index))) {
            throw new IllegalArgumentException("The devtool subcommand is missing");
        }

        String operation = tokens.get(index++);
        if (!DevtoolCommandBuilder.isOperation(operation)) {
            config.operation = "custom";
            List<String> custom = new ArrayList<>();
            custom.add(renderToken(operation));
            while (index < tokens.size() && !isConnector(tokens.get(index))) {
                custom.add(renderToken(tokens.get(index++)));
            }
            config.customArguments = String.join(" ", custom);
        } else {
            config.operation = operation;
            parseOperation(tokens, index, config, notices);
        }

        config.extraGlobalArguments = String.join(" ", globalExtras);
        if (config.repo.isEmpty() && !workdir.isEmpty()) config.repo = workdir;
        if (config.repo.isEmpty()) config.repo = "$HOME/Code/sims3tools";
        if (workdir.isEmpty()) workdir = config.useChroot ? "~/" : config.repo;
        config.workdir = workdir;

        String notice = notices.isEmpty() ? "Command imported into the visual builder" : String.join(". ", notices);
        return new ImportResult(config, workdir, notice);
    }

    private static void parseOperation(List<String> tokens, int start, DevtoolRunConfig config,
                                       List<String> notices) {
        List<String> extras = new ArrayList<>();
        int index = start;
        boolean artifactSet = false;
        while (index < tokens.size()) {
            String token = tokens.get(index);
            if (isConnector(token)) {
                notices.add("Trailing shell command was not imported");
                break;
            }
            if (token.equals("--validate")) {
                config.validate = true;
                index++;
                continue;
            }
            if (token.equals("--no-validate")) {
                config.validate = false;
                index++;
                continue;
            }
            if (token.equals("--commit") && (config.operation.equals("apply-overlay") || config.operation.equals("apply-overlays"))) {
                config.commit = true;
                config.commitMessage = requireValue(tokens, ++index, token);
                index++;
                continue;
            }
            if ((token.equals("-m") || token.equals("--message")) && config.operation.equals("commit")) {
                config.commitMessage = requireValue(tokens, ++index, token);
                index++;
                continue;
            }
            if ((config.operation.equals("apply-overlay") || config.operation.equals("apply-overlays"))
                    && !artifactSet && !token.startsWith("-")) {
                config.artifact = token;
                artifactSet = true;
                index++;
                continue;
            }
            extras.add(renderToken(token));
            index++;
        }
        config.extraOperationArguments = String.join(" ", extras);
        if ((config.operation.equals("apply-overlay") || config.operation.equals("apply-overlays"))
                && config.artifact.isEmpty()) {
            throw new IllegalArgumentException("The pasted apply command has no overlay or bundle path");
        }
    }

    private static DevtoolRunConfig emptyConfig() {
        DevtoolRunConfig config = new DevtoolRunConfig();
        config.operation = "status";
        config.repo = "";
        config.workdir = "";
        config.target = "";
        config.artifact = "";
        config.commitMessage = "";
        config.customArguments = "";
        config.environmentAssignments = "";
        config.extraGlobalArguments = "";
        config.extraOperationArguments = "";
        config.chrootName = "androidos";
        config.useChroot = false;
        config.copy = false;
        config.copyViaTermux = false;
        config.autoHud = false;
        config.validate = false;
        config.commit = false;
        config.assumeYes = false;
        config.overlay = true;
        return config;
    }

    static List<String> tokenize(String source) {
        List<String> tokens = new ArrayList<>();
        if (source == null) return tokens;
        StringBuilder token = new StringBuilder();
        boolean single = false;
        boolean doubled = false;
        boolean escaped = false;
        for (int i = 0; i < source.length(); i++) {
            char c = source.charAt(i);
            if (escaped) {
                if (c != '\n' && c != '\r') token.append(c);
                escaped = false;
                continue;
            }
            if (!single && c == '\\') {
                escaped = true;
                continue;
            }
            if (!doubled && c == '\'') {
                single = !single;
                continue;
            }
            if (!single && c == '"') {
                doubled = !doubled;
                continue;
            }
            if (!single && !doubled && c == '#') {
                if (token.length() == 0) {
                    while (i < source.length() && source.charAt(i) != '\n') i++;
                    flush(tokens, token);
                    continue;
                }
            }
            if (!single && !doubled && Character.isWhitespace(c)) {
                flush(tokens, token);
                continue;
            }
            if (!single && !doubled && c == '&' && i + 1 < source.length() && source.charAt(i + 1) == '&') {
                flush(tokens, token);
                tokens.add("&&");
                i++;
                continue;
            }
            if (!single && !doubled && (c == ';' || c == '|')) {
                flush(tokens, token);
                tokens.add(String.valueOf(c));
                continue;
            }
            token.append(c);
        }
        if (escaped) token.append('\\');
        if (single || doubled) throw new IllegalArgumentException("The pasted command has an unclosed quote");
        flush(tokens, token);
        return tokens;
    }

    private static void flush(List<String> tokens, StringBuilder token) {
        if (token.length() == 0) return;
        tokens.add(token.toString());
        token.setLength(0);
    }

    private static int findExecutable(List<String> tokens) {
        for (int i = 0; i < tokens.size(); i++) {
            String token = tokens.get(i);
            if (isConnector(token) || isPromptMarker(token) || token.equals("\\") || token.equals("env")) {
                continue;
            }
            if (token.equals("cd")) {
                if (i + 1 >= tokens.size() || isConnector(tokens.get(i + 1))) return -1;
                i++;
                continue;
            }
            if (ASSIGNMENT.matcher(token).matches()) {
                continue;
            }
            String name = basename(token);
            if (name.equals("devtool") || name.equals("devtool-androidos")) return i;
            return -1;
        }
        return -1;
    }

    private static int findKnownOperation(List<String> tokens, int start) {
        for (int i = start; i < tokens.size(); i++) {
            if (isConnector(tokens.get(i))) return -1;
            if (DevtoolCommandBuilder.isOperation(tokens.get(i))) return i;
        }
        return -1;
    }

    private static String basename(String value) {
        int slash = value.lastIndexOf('/');
        return slash >= 0 ? value.substring(slash + 1) : value;
    }

    private static String requireValue(List<String> tokens, int index, String option) {
        if (index >= tokens.size() || isConnector(tokens.get(index))) {
            throw new IllegalArgumentException(option + " is missing its value");
        }
        return tokens.get(index);
    }

    private static boolean isConnector(String token) {
        return token.equals("&&") || token.equals(";") || token.equals("|");
    }

    private static boolean isPromptMarker(String token) {
        return token.equals("$") || token.equals("❯") || token.equals(">") || token.equals("#");
    }

    private static String renderToken(String token) {
        return token.contains("$") ? DevtoolCommandBuilder.shellPath(token)
                : DevtoolCommandBuilder.shellLiteral(token);
    }

    private static String renderAssignment(String token) {
        int equals = token.indexOf('=');
        if (equals < 1) return token;
        String key = token.substring(0, equals);
        String value = token.substring(equals + 1);
        if (value.matches("[A-Za-z0-9_./:@%+=,-]+")) return key + "=" + value;
        if (value.contains("$")) return key + "=" + DevtoolCommandBuilder.shellPath(value);
        return key + "=" + DevtoolCommandBuilder.shellLiteral(value);
    }
}
