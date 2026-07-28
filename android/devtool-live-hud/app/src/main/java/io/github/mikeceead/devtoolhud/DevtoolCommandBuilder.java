package io.github.mikeceead.devtoolhud;

import java.util.ArrayList;
import java.util.List;

final class DevtoolCommandBuilder {
    static final String[] PROJECT_TYPES = new String[]{"auto", "dotnet", "android", "gradle", "python", "command"};
    static final String[] EXECUTION_ENVIRONMENTS = new String[]{"auto", "native-termux", "chroot", "desktop-linux", "android-device", "external-device"};
    static final String[] OPERATIONS = new String[]{
            "apply-overlay", "apply-overlays", "validate", "build", "test", "lint",
            "restore", "run", "package", "clean", "inspect", "status", "doctor", "targets", "history", "commit", "custom"
    };

    private DevtoolCommandBuilder() {}

    static String build(DevtoolRunConfig config) {
        String validationError = config.validationError();
        if (!validationError.isEmpty()) throw new IllegalArgumentException(validationError);
        List<String> parts = new ArrayList<>();
        String environment = clean(config.environmentAssignments, "");
        if (!environment.isEmpty()) parts.add(environment);

        if (config.useChroot) {
            parts.add("devtool-androidos");
            parts.add("--chroot");
            parts.add(shellLiteral(clean(config.chrootName, "androidos")));
        } else {
            parts.add("devtool");
        }

        String projectType = clean(config.projectType, "auto");
        if (!projectType.equals("auto")) {
            parts.add("--project-type");
            parts.add(shellLiteral(projectType));
        }
        String executionEnvironment = clean(config.executionEnvironment, "auto");
        if (!config.useChroot && !executionEnvironment.equals("auto")) {
            parts.add("--execution-environment");
            parts.add(shellLiteral(executionEnvironment));
        }

        if (config.copy) {
            parts.add("--copy");
            if (config.copyViaTermux) {
                parts.add("--copy-via");
                parts.add("termux");
            }
        }
        if (config.autoHud) {
            parts.add("--auto-hud");
            if (!clean(config.extraGlobalArguments, "").contains("--hud-mode")) {
                parts.add("--hud-mode");
                parts.add("android-bridge");
            }
        } else {
            parts.add("--no-hud");
        }
        if (config.assumeYes) parts.add("--yes");
        String repo = clean(config.repo, "$HOME/Code/sims3tools");
        parts.add("-r");
        parts.add(shellPath(repo));
        String target = clean(config.target, "");
        if (!target.isEmpty()) {
            parts.add("--target");
            parts.add(shellLiteral(target));
        }
        String extraGlobal = clean(config.extraGlobalArguments, "");
        if (!extraGlobal.isEmpty()) parts.add(extraGlobal);

        String operation = clean(config.operation, "status");
        if (operation.equals("custom")) {
            String custom = clean(config.customArguments, "");
            if (custom.isEmpty()) throw new IllegalArgumentException("Custom arguments are empty");
            parts.add(custom);
            String extraOperation = clean(config.extraOperationArguments, "");
            if (!extraOperation.isEmpty()) parts.add(extraOperation);
            return String.join(" ", parts);
        }

        parts.add(operation);
        if (operation.equals("apply-overlay") || operation.equals("apply-overlays")) {
            String artifact = clean(config.artifact, "");
            if (artifact.isEmpty()) throw new IllegalArgumentException("Overlay or bundle path is required");
            parts.add(shellPath(artifact));
            if (config.validate) parts.add("--validate");
            if (config.commit) {
                String message = clean(config.commitMessage, "");
                parts.add("--commit");
                if (!message.isEmpty()) parts.add(shellLiteral(message));
            }
        } else if (operation.equals("commit")) {
            String message = clean(config.commitMessage, "");
            if (message.isEmpty()) throw new IllegalArgumentException("Commit message is required");
            parts.add(shellLiteral(message));
        }
        appendTestSelection(parts, config, operation);
        String extraOperation = clean(config.extraOperationArguments, "");
        if (!extraOperation.isEmpty()) parts.add(extraOperation);
        return String.join(" ", parts);
    }

    private static void appendTestSelection(List<String> parts, DevtoolRunConfig config, String operation) {
        boolean test = operation.equals("test");
        boolean validation = operation.equals("validate") || operation.equals("apply-overlay") || operation.equals("apply-overlays");
        if (!test && !validation) return;
        String profile = clean(config.testProfile, "");
        if (!profile.isEmpty()) {
            parts.add(test ? "--profile" : "--test-profile");
            parts.add(shellLiteral(profile));
        }
        if (!test) return;
        if (config.listTests) parts.add("--list-tests");
        for (String value : splitValues(config.testSelectors)) { parts.add("--select"); parts.add(shellLiteral(value)); }
        for (String value : splitValues(config.testTags)) { parts.add("--tag"); parts.add(shellLiteral(value)); }
        for (String value : splitValues(config.testExcludeTags)) { parts.add("--exclude-tag"); parts.add(shellLiteral(value)); }
        String path = clean(config.testPath, "");
        if (!path.isEmpty()) { parts.add("--path"); parts.add(shellPath(path)); }
        String className = clean(config.testClass, "");
        if (!className.isEmpty()) { parts.add("--class"); parts.add(shellLiteral(className)); }
        String method = clean(config.testMethod, "");
        if (!method.isEmpty()) { parts.add("--method"); parts.add(shellLiteral(method)); }
        if (config.instrumented) parts.add("--instrumented");
    }

    private static List<String> splitValues(String value) {
        List<String> result = new ArrayList<>();
        String clean = value == null ? "" : value.trim();
        if (clean.isEmpty()) return result;
        for (String item : clean.split("[,\n]")) {
            String entry = item.trim();
            if (!entry.isEmpty()) result.add(entry);
        }
        return result;
    }

    static boolean isOperation(String value) {
        if (value == null) return false;
        for (String operation : OPERATIONS) {
            if (!operation.equals("custom") && operation.equals(value)) return true;
        }
        return false;
    }

    static String shellPath(String value) {
        String clean = value == null ? "" : value;
        if (clean.startsWith("~/")) clean = "$HOME/" + clean.substring(2);
        if (clean.matches("[A-Za-z0-9_./$:@%+=,-]+")) return clean;
        if (clean.contains("$")) {
            return "\"" + clean.replace("\\", "\\\\")
                    .replace("\"", "\\\"")
                    .replace("`", "\\`") + "\"";
        }
        return shellLiteral(clean);
    }

    static String shellLiteral(String value) {
        String clean = value == null ? "" : value;
        if (clean.matches("[A-Za-z0-9_./:@%+=,-]+")) return clean;
        return "'" + clean.replace("'", "'\\''") + "'";
    }

    private static String clean(String value, String fallback) {
        String clean = value == null ? "" : value.trim();
        return clean.isEmpty() ? fallback : clean;
    }
}
