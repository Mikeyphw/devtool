package io.github.mikeceead.devtoolhud;

import java.util.Locale;

final class DevtoolRunConfig {
    String operation = "apply-overlay";
    String projectType = "auto";
    String executionEnvironment = "auto";
    String repo = "$HOME/Code/sims3tools";
    String workdir = "";
    String target = "s3atelier_avalonia_S3Atelier_Modern";
    String artifact = "$HOME/Downloads/overlay.zip";
    String commitMessage = "Apply overlay";
    String customArguments = "";
    String environmentAssignments = "";
    String extraGlobalArguments = "";
    String extraOperationArguments = "";
    String chrootName = "androidos";
    String testProfile = "";
    String testSelectors = "";
    String testTags = "";
    String testExcludeTags = "";
    String testPath = "";
    String testClass = "";
    String testMethod = "";
    boolean useChroot = false;
    boolean useRootBridge = false;
    boolean copy = true;
    boolean copyViaTermux = true;
    boolean autoHud = true;
    boolean validate = true;
    boolean commit = true;
    boolean assumeYes = true;
    boolean overlay = true;
    boolean listTests = false;
    boolean instrumented = false;

    String summary() {
        if (operation.equals("apply-overlay") || operation.equals("apply-overlays")) {
            return operation + " · " + shortName(artifact);
        }
        return operation + (target.trim().isEmpty() ? "" : " · " + target.trim());
    }

    String validationError() {
        String environment = clean(executionEnvironment, "auto").toLowerCase(Locale.ROOT);
        String type = clean(projectType, "auto").toLowerCase(Locale.ROOT);
        String op = clean(operation, "status").toLowerCase(Locale.ROOT);
        if (useChroot && !(environment.equals("auto") || environment.equals("chroot"))) {
            return "Direct chroot mode cannot be combined with execution environment " + environment;
        }
        if (useChroot && clean(chrootName, "").isEmpty()) {
            return "Chroot name is required when direct chroot mode is enabled";
        }
        if (copyViaTermux && !copy) {
            return "Termux clipboard routing requires Copy result";
        }
        if (instrumented && !op.equals("test")) {
            return "Android instrumentation is available only for test runs";
        }
        if (instrumented && !(type.equals("auto") || type.equals("android"))) {
            return "Android instrumentation requires project type Android or Auto";
        }
        if (instrumented && (environment.equals("chroot") || environment.equals("desktop-linux"))) {
            return "Instrumentation must run on the Android device, not inside a chroot or desktop environment";
        }
        return "";
    }

    DevtoolRunConfig copy() {
        DevtoolRunConfig config = new DevtoolRunConfig();
        config.operation = operation;
        config.projectType = projectType;
        config.executionEnvironment = executionEnvironment;
        config.repo = repo;
        config.workdir = workdir;
        config.target = target;
        config.artifact = artifact;
        config.commitMessage = commitMessage;
        config.customArguments = customArguments;
        config.environmentAssignments = environmentAssignments;
        config.extraGlobalArguments = extraGlobalArguments;
        config.extraOperationArguments = extraOperationArguments;
        config.chrootName = chrootName;
        config.testProfile = testProfile;
        config.testSelectors = testSelectors;
        config.testTags = testTags;
        config.testExcludeTags = testExcludeTags;
        config.testPath = testPath;
        config.testClass = testClass;
        config.testMethod = testMethod;
        config.useChroot = useChroot;
        config.useRootBridge = useRootBridge;
        config.copy = copy;
        config.copyViaTermux = copyViaTermux;
        config.autoHud = autoHud;
        config.validate = validate;
        config.commit = commit;
        config.assumeYes = assumeYes;
        config.overlay = overlay;
        config.listTests = listTests;
        config.instrumented = instrumented;
        return config;
    }

    private static String shortName(String value) {
        String clean = value == null ? "" : value.trim();
        int slash = Math.max(clean.lastIndexOf('/'), clean.lastIndexOf('\\'));
        return slash >= 0 ? clean.substring(slash + 1) : clean;
    }

    private static String clean(String value, String fallback) {
        String clean = value == null ? "" : value.trim();
        return clean.isEmpty() ? fallback : clean;
    }
}
