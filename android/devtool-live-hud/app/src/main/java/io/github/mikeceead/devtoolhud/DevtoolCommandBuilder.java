package io.github.mikeceead.devtoolhud;

import java.util.ArrayList;
import java.util.List;

final class DevtoolCommandBuilder {
    static final String[] OPERATIONS = new String[]{
            "apply-overlay", "apply-overlays", "validate", "build", "test",
            "restore", "status", "doctor", "targets", "history", "commit", "custom"
    };

    private DevtoolCommandBuilder() {}

    static String build(DevtoolRunConfig config) {
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

        if (config.copy) {
            parts.add("--copy");
            if (config.copyViaTermux) {
                parts.add("--copy-via");
                parts.add("termux");
            }
        }
        if (config.autoHud) {
            parts.add("--auto-hud");
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
                if (message.isEmpty()) throw new IllegalArgumentException("Commit message is required");
                parts.add("--commit");
                parts.add(shellLiteral(message));
            }
        } else if (operation.equals("commit")) {
            String message = clean(config.commitMessage, "");
            if (!message.isEmpty()) {
                parts.add("-m");
                parts.add(shellLiteral(message));
            }
        }
        String extraOperation = clean(config.extraOperationArguments, "");
        if (!extraOperation.isEmpty()) parts.add(extraOperation);
        return String.join(" ", parts);
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
