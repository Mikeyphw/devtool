package io.github.mikeceead.devtoolhud;

final class DevtoolRunConfig {
    String operation = "apply-overlay";
    String repo = "$HOME/Code/sims3tools";
    String workdir = "";
    String target = "s3atelier_avalonia_S3Atelier_Modern";
    String artifact = "$HOME/Downloads/overlay.zip";
    String commitMessage = "Apply overlay";
    String customArguments = "";
    String environmentAssignments = "DEVTOOL_HUD_SINGLETON=kill";
    String extraGlobalArguments = "";
    String extraOperationArguments = "";
    String chrootName = "androidos";
    boolean useChroot = false;
    boolean useRootBridge = false;
    boolean copy = true;
    boolean copyViaTermux = true;
    boolean autoHud = true;
    boolean validate = true;
    boolean commit = true;
    boolean assumeYes = true;
    boolean overlay = true;

    String summary() {
        if (operation.equals("apply-overlay") || operation.equals("apply-overlays")) {
            return operation + " · " + shortName(artifact);
        }
        return operation + (target.trim().isEmpty() ? "" : " · " + target.trim());
    }

    private static String shortName(String value) {
        String clean = value == null ? "" : value.trim();
        int slash = Math.max(clean.lastIndexOf('/'), clean.lastIndexOf('\\'));
        return slash >= 0 ? clean.substring(slash + 1) : clean;
    }
}
