package io.github.mikeceead.devtoolhud;

import android.content.Context;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

final class RepositoryScanner {
    private final RootCommandBroker broker;

    RepositoryScanner(Context context) { broker = new RootCommandBroker(context); }

    boolean rootAvailable() { return broker.available(); }

    List<RepositoryLocation> discoverAll() {
        Map<String, RepositoryLocation> values = new LinkedHashMap<>();
        for (RepositoryLocation location : broker.discoverRepositories("native-termux", "", "termux")) {
            values.put(location.id(), location);
        }
        for (String chroot : broker.discoverChrootNames()) {
            for (RepositoryLocation location : broker.discoverRepositories("chroot", chroot, "")) {
                values.put(location.id(), location);
            }
        }
        return new ArrayList<>(values.values());
    }

    RepositoryRecord inspect(RepositoryRecord current) {
        RepositoryInspection inspection = broker.inspect(current.location());
        RepositoryRecord next = current;
        next.lastScannedAt = System.currentTimeMillis();
        next.lastError = inspection.error;
        next.status = inspection.status;
        next.dirty = inspection.dirty;
        next.gitRoot = inspection.gitRoot;
        next.branch = inspection.branch;
        next.upstream = inspection.upstream;
        next.remote = inspection.remote;
        next.aheadCount = inspection.ahead;
        next.behindCount = inspection.behind;
        if (!inspection.projectType.isEmpty()) next.projectType = inspection.projectType;
        if (!inspection.gitRoot.isEmpty()) {
            next.canonicalPath = inspection.gitRoot;
            next.displayName = baseName(inspection.gitRoot);
        } else if (next.displayName.isEmpty()) {
            next.displayName = baseName(next.canonicalPath);
        }
        return next;
    }

    List<String> directories(RepositoryLocation location) {
        return broker.listDirectories(location);
    }

    private static String baseName(String path) {
        String clean = RepositoryLocation.clean(path);
        int slash = clean.lastIndexOf('/');
        return slash >= 0 && slash + 1 < clean.length() ? clean.substring(slash + 1) : clean;
    }
}
