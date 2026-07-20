package io.github.mikeceead.devtoolhud;

final class RepositoryRecord {
    String repositoryId = "";
    String displayName = "";
    String environment = "native-termux";
    String chrootName = "";
    String linuxUser = "termux";
    String configuredPath = "";
    String canonicalPath = "";
    String hostPath = "";
    String gitRoot = "";
    String branch = "";
    String upstream = "";
    String remote = "";
    String status = "checking";
    String projectType = "auto";
    String lastError = "";
    boolean dirty;
    boolean saved;
    int aheadCount;
    int behindCount;
    int activeRunCount;
    long lastUsedAt;
    long lastScannedAt;

    RepositoryLocation location() {
        return new RepositoryLocation(environment, chrootName, linuxUser, canonicalPath, hostPath);
    }

    String name() {
        if (!displayName.isEmpty()) return displayName;
        String path = gitRoot.isEmpty() ? canonicalPath : gitRoot;
        int slash = path.lastIndexOf('/');
        return slash >= 0 && slash + 1 < path.length() ? path.substring(slash + 1) : path;
    }

    boolean liveOnly() { return !saved && activeRunCount > 0; }
}
