package io.github.mikeceead.devtoolhud;

import android.annotation.SuppressLint;

import org.json.JSONException;
import org.json.JSONObject;

@SuppressLint("SdCardPath")
final class RepositoryPathResolver {
    static final String FAMILY_TERMUX = "termux";
    static final String FAMILY_CHROOT = "chroot";
    static final String FAMILY_ROOT = "root";
    static final String FAMILY_HOME = "home";
    static final String FAMILY_UNKNOWN = "unknown";

    private RepositoryPathResolver() {}

    static RepositoryPathResolution resolve(RepositoryRecord repository) {
        return analyze(repository);
    }

    static RepositoryPathResolution analyze(RepositoryRecord repository) {
        RepositoryPathResolution resolution = new RepositoryPathResolution();
        if (repository == null) {
            resolution.status = RepositoryPathResolution.STATUS_FAIL;
            resolution.issueKey = "repo_missing_record";
            resolution.title = "Repository record is unavailable";
            resolution.summary = "The repository entry could not be loaded from the Android database.";
            resolution.recommendation = "Return to the repository list and refresh before repairing metadata.";
            return resolution;
        }
        RepositoryLocation location = repository.location();
        resolution.repositoryId = clean(repository.repositoryId);
        resolution.environment = clean(location.environment);
        resolution.chrootName = clean(location.chrootName);
        resolution.linuxUser = clean(location.linuxUser);
        resolution.configuredPath = clean(repository.configuredPath);
        resolution.canonicalPath = clean(repository.canonicalPath);
        resolution.hostPath = clean(repository.hostPath);
        resolution.gitRoot = clean(repository.gitRoot);
        resolution.detectedPathFamily = pathFamily(repository.canonicalPath, repository.hostPath);
        resolution.expectedPathFamily = expectedFamily(location);

        if (location.environment.equals("chroot") && termuxHostPath(repository.canonicalPath)) {
            fail(resolution, "repo_termux_path_saved_as_chroot",
                    "Termux path saved as chroot repository",
                    "This repository is configured as chroot, but the stored path is visible to Termux/root rather than inside the chroot.",
                    "Reclassify this repository as Termux or replace the path with the chroot-visible path.");
            addReclassifyTermux(resolution, repository.canonicalPath);
            addUpdateChrootPath(resolution, termuxHostToChrootGuess(repository.canonicalPath, location.linuxUser));
            return resolution;
        }
        if (location.environment.equals("root") && termuxHostPath(repository.canonicalPath)) {
            warn(resolution, "repo_termux_path_saved_as_root",
                    "Termux path saved as root repository",
                    "This repository is configured as root, but the stored path is inside Termux home.",
                    "Reclassify this repository as Termux unless root execution is intentionally required.");
            addReclassifyTermux(resolution, repository.canonicalPath);
            addReclassifyRoot(resolution, repository.canonicalPath);
            return resolution;
        }
        if (!location.environment.equals("chroot") && linuxHomePath(repository.canonicalPath)
                && !termuxHomeLike(repository.canonicalPath)) {
            warn(resolution, "repo_chroot_path_saved_as_termux_or_root",
                    "Linux home path may need chroot metadata",
                    "The path looks like /home/<user>/... but this repository is not configured as chroot.",
                    "Reclassify as chroot when Git runs only inside the Linux environment, or update the path to the Termux-visible location.");
            addReclassifyChroot(resolution, defaultChroot(location), RepositoryLocation.inferUser(repository.canonicalPath), repository.canonicalPath);
            addReclassifyRoot(resolution, repository.canonicalPath);
            return resolution;
        }
        if (location.environment.equals("native-termux") && rootHostPath(repository.canonicalPath)) {
            warn(resolution, "repo_root_path_saved_as_termux",
                    "Root path saved as Termux repository",
                    "The path is outside the Termux home and may require root execution rather than native Termux.",
                    "Reclassify this repository as root if Termux cannot access the path directly.");
            addReclassifyRoot(resolution, repository.canonicalPath);
            return resolution;
        }
        if (location.environment.equals("native-termux") && termuxHostPath(repository.canonicalPath)) {
            String normalized = RepositoryLocation.normalizeTermuxPath(repository.canonicalPath);
            if (!normalized.equals(repository.canonicalPath)) {
                warn(resolution, "repo_termux_host_path_can_be_normalized",
                        "Termux path can be normalized",
                        "The repository is Termux-native but stores the long app-private home path.",
                        "Normalize it to ~/... so the same repository survives Termux home relocation better.");
                addUpdateTermuxPath(resolution, normalized);
                return resolution;
            }
        }
        if (invalidHomeExpansion(repository.canonicalPath, location.environment)) {
            warn(resolution, "repo_invalid_home_expansion",
                    "Home path expansion looks inconsistent",
                    "The stored path uses a home alias that does not match the selected execution namespace.",
                    "Repair the path to the namespace-visible absolute path before retrying Git actions.");
            addUpdateTermuxPath(resolution, RepositoryLocation.normalizeTermuxPath(repository.canonicalPath));
            addReclassifyChroot(resolution, defaultChroot(location), location.linuxUser, expandChrootHome(repository.canonicalPath, location.linuxUser));
            return resolution;
        }
        if (!repository.gitRoot.isEmpty() && !repository.gitRoot.equals(repository.canonicalPath)) {
            warn(resolution, "repo_discovered_git_root_available",
                    "Discovered Git root differs from stored path",
                    "The scanner found a Git root that differs from the stored repository path.",
                    "Use the discovered Git root to keep Git actions anchored at the repository root.");
            addUseDiscoveredGitRoot(resolution, repository);
            return resolution;
        }
        if (repository.status.equals("missing") || repository.status.equals("error")
                || lower(repository.lastError).contains("does not exist")
                || lower(repository.lastError).contains("no such file")) {
            warn(resolution, "repo_missing_path_repair_available",
                    "Repository path needs review",
                    "The latest scan reported a missing path or repository error.",
                    "Use Browse files as root to select a namespace-visible path, or apply a matching reclassification below.");
            addLikelyRepairs(resolution, repository);
            return resolution;
        }
        pass(resolution, "repo_path_metadata_ok", "Repository metadata looks consistent",
                "The stored path, host path and environment do not show a known namespace mismatch.",
                "No metadata repair is needed before refreshing this repository.");
        return resolution;
    }

    static JSONObject supportSummary(RepositoryRecord repository) throws JSONException {
        return analyze(repository).toJson();
    }

    static RepositoryPathResolution.RepairAction findAction(RepositoryRecord repository, String actionId) {
        String wanted = clean(actionId);
        for (RepositoryPathResolution.RepairAction action : analyze(repository).actions) {
            if (action.actionId.equals(wanted)) return action;
        }
        return null;
    }

    private static void addLikelyRepairs(RepositoryPathResolution resolution, RepositoryRecord repository) {
        if (termuxHostPath(repository.canonicalPath) || repository.canonicalPath.startsWith("~/")) {
            addReclassifyTermux(resolution, repository.canonicalPath);
        }
        if (linuxHomePath(repository.canonicalPath)) {
            addReclassifyChroot(resolution, defaultChroot(repository.location()),
                    RepositoryLocation.inferUser(repository.canonicalPath), repository.canonicalPath);
        }
        if (repository.canonicalPath.startsWith("/")) addReclassifyRoot(resolution, repository.canonicalPath);
    }

    private static void addReclassifyTermux(RepositoryPathResolution resolution, String path) {
        RepositoryLocation location = RepositoryLocation.termux(path);
        add(resolution, RepositoryPathResolution.RepairAction.KIND_RECLASSIFY_TERMUX,
                "Reclassify as Termux", "Store this repository as native Termux and use the Termux-visible path.",
                location.environment, location.chrootName, location.linuxUser, location.path, location.hostPath);
    }

    private static void addUpdateTermuxPath(RepositoryPathResolution resolution, String path) {
        RepositoryLocation location = RepositoryLocation.termux(path);
        add(resolution, RepositoryPathResolution.RepairAction.KIND_UPDATE_PATH,
                "Normalize Termux path", "Keep this as Termux but update the stored path to the normalized ~/... form.",
                location.environment, location.chrootName, location.linuxUser, location.path, location.hostPath);
    }

    private static void addReclassifyChroot(RepositoryPathResolution resolution, String chroot, String user, String path) {
        RepositoryLocation location = RepositoryLocation.chroot(chroot, user, path);
        add(resolution, RepositoryPathResolution.RepairAction.KIND_RECLASSIFY_CHROOT,
                "Reclassify as chroot", "Store this repository as chroot using the Linux-visible path and user.",
                location.environment, location.chrootName, location.linuxUser, location.path, location.hostPath);
    }

    private static void addUpdateChrootPath(RepositoryPathResolution resolution, String path) {
        String user = RepositoryLocation.inferUser(path);
        if (user.isEmpty() || user.equals("root")) user = clean(resolution.linuxUser).isEmpty() ? "root" : resolution.linuxUser;
        RepositoryLocation location = RepositoryLocation.chroot(defaultChroot(resolution.chrootName), user, path);
        add(resolution, RepositoryPathResolution.RepairAction.KIND_UPDATE_PATH,
                "Use chroot path", "Keep this as chroot but update the repository path to a Linux-visible path.",
                location.environment, location.chrootName, location.linuxUser, location.path, location.hostPath);
    }

    private static void addReclassifyRoot(RepositoryPathResolution resolution, String path) {
        RepositoryLocation location = RepositoryLocation.root(path);
        add(resolution, RepositoryPathResolution.RepairAction.KIND_RECLASSIFY_ROOT,
                "Reclassify as root", "Store this repository as root-accessed Android filesystem path.",
                location.environment, location.chrootName, location.linuxUser, location.path, location.hostPath);
    }

    private static void addUseDiscoveredGitRoot(RepositoryPathResolution resolution, RepositoryRecord repository) {
        RepositoryLocation current = repository.location();
        RepositoryLocation location;
        if (current.environment.equals("chroot")) location = RepositoryLocation.chroot(current.chrootName, current.linuxUser, repository.gitRoot);
        else if (current.environment.equals("root")) location = RepositoryLocation.root(repository.gitRoot);
        else location = RepositoryLocation.termux(repository.gitRoot);
        add(resolution, RepositoryPathResolution.RepairAction.KIND_USE_DISCOVERED_GIT_ROOT,
                "Use discovered Git root", "Update the stored path to the repository root discovered by Git inspection.",
                location.environment, location.chrootName, location.linuxUser, location.path, location.hostPath);
    }

    private static void add(RepositoryPathResolution resolution, String kind, String label,
                            String description, String environment, String chrootName,
                            String linuxUser, String path, String hostPath) {
        RepositoryPathResolution.RepairAction action = new RepositoryPathResolution.RepairAction();
        action.kind = kind;
        action.actionId = kind + ":" + clean(environment) + ":" + clean(chrootName) + ":"
                + clean(linuxUser) + ":" + clean(path);
        action.label = label;
        action.description = description;
        action.environment = clean(environment);
        action.chrootName = clean(chrootName);
        action.linuxUser = clean(linuxUser);
        action.path = clean(path);
        action.hostPath = clean(hostPath);
        action.primary = resolution.actions.isEmpty();
        resolution.actions.add(action);
    }

    private static void pass(RepositoryPathResolution resolution, String key, String title,
                             String summary, String recommendation) {
        resolution.status = RepositoryPathResolution.STATUS_PASS;
        resolution.issueKey = key;
        resolution.title = title;
        resolution.summary = summary;
        resolution.recommendation = recommendation;
    }

    private static void warn(RepositoryPathResolution resolution, String key, String title,
                             String summary, String recommendation) {
        resolution.status = RepositoryPathResolution.STATUS_WARN;
        resolution.issueKey = key;
        resolution.title = title;
        resolution.summary = summary;
        resolution.recommendation = recommendation;
    }

    private static void fail(RepositoryPathResolution resolution, String key, String title,
                             String summary, String recommendation) {
        resolution.status = RepositoryPathResolution.STATUS_FAIL;
        resolution.issueKey = key;
        resolution.title = title;
        resolution.summary = summary;
        resolution.recommendation = recommendation;
    }

    private static String pathFamily(String path, String hostPath) {
        String clean = clean(path);
        if (termuxHostPath(clean) || termuxHomeLike(clean) || termuxHostPath(hostPath)) return FAMILY_TERMUX;
        if (linuxHomePath(clean)) return FAMILY_CHROOT;
        if (clean.startsWith("/data/") || clean.startsWith("/sdcard/") || clean.startsWith("/storage/")) return FAMILY_ROOT;
        if (clean.startsWith("/")) return FAMILY_ROOT;
        return FAMILY_UNKNOWN;
    }

    private static String expectedFamily(RepositoryLocation location) {
        if (location.environment.equals("native-termux")) return FAMILY_TERMUX;
        if (location.environment.equals("chroot")) return FAMILY_CHROOT;
        if (location.environment.equals("root")) return FAMILY_ROOT;
        return FAMILY_UNKNOWN;
    }

    private static boolean termuxHostPath(String path) {
        return clean(path).startsWith(RepositoryLocation.TERMUX_HOME + "/")
                || clean(path).equals(RepositoryLocation.TERMUX_HOME);
    }

    private static boolean termuxHomeLike(String path) {
        String clean = clean(path);
        return clean.equals("~") || clean.equals("~/") || clean.startsWith("~/")
                || clean.equals("$HOME") || clean.startsWith("$HOME/")
                || clean.startsWith("${HOME}/");
    }

    private static boolean linuxHomePath(String path) {
        return clean(path).startsWith("/home/") || clean(path).equals("/root")
                || clean(path).startsWith("/root/");
    }

    private static boolean rootHostPath(String path) {
        String clean = clean(path);
        return clean.startsWith("/data/") || clean.startsWith("/sdcard/") || clean.startsWith("/storage/");
    }

    private static boolean invalidHomeExpansion(String path, String environment) {
        String clean = clean(path);
        if (environment.equals("chroot")) return termuxHomeLike(clean) || termuxHostPath(clean);
        if (environment.equals("native-termux")) return clean.startsWith("/home/") || clean.startsWith("/root/");
        return false;
    }

    private static String termuxHostToChrootGuess(String path, String user) {
        String clean = clean(path);
        String suffix = termuxHostPath(clean) ? clean.substring(RepositoryLocation.TERMUX_HOME.length()) : clean;
        if (suffix.startsWith("/")) suffix = suffix.substring(1);
        String linuxUser = clean(user);
        if (linuxUser.isEmpty() || linuxUser.equals("termux") || linuxUser.equals("root")) linuxUser = "mike";
        return "/home/" + linuxUser + (suffix.isEmpty() ? "" : "/" + suffix);
    }

    private static String expandChrootHome(String path, String user) {
        String clean = clean(path);
        String linuxUser = clean(user);
        if (linuxUser.isEmpty() || linuxUser.equals("termux")) linuxUser = "mike";
        if (clean.equals("~") || clean.equals("~/")) return "/home/" + linuxUser;
        if (clean.startsWith("~/")) return "/home/" + linuxUser + "/" + clean.substring(2);
        if (clean.startsWith("$HOME/")) return "/home/" + linuxUser + "/" + clean.substring(6);
        if (clean.startsWith("${HOME}/")) return "/home/" + linuxUser + "/" + clean.substring(8);
        return clean;
    }

    private static String defaultChroot(RepositoryLocation location) {
        String value = clean(location.chrootName);
        return value.isEmpty() ? "androidos" : value;
    }

    private static String defaultChroot(String chrootName) {
        String value = clean(chrootName);
        return value.isEmpty() ? "androidos" : value;
    }

    private static String lower(String value) { return clean(value).toLowerCase(java.util.Locale.ROOT); }
    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
