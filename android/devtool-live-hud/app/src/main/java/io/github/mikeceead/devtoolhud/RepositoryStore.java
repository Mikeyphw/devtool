package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.Intent;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

final class RepositoryStore {
    static final String ACTION_UPDATED = "io.github.mikeceead.devtoolhud.REPOSITORIES_UPDATED";
    static final String EXTRA_ERROR = "error";
    private static final ExecutorService EXECUTOR = Executors.newSingleThreadExecutor();
    private static final AtomicBoolean BUSY = new AtomicBoolean();

    private RepositoryStore() {}

    static List<RepositoryRecord> all(Context context) {
        syncLiveRuns(context);
        return DevtoolDatabase.get(context).repositories();
    }

    static RepositoryRecord find(Context context, String id) {
        syncLiveRuns(context);
        return DevtoolDatabase.get(context).repository(id == null ? "" : id);
    }

    static boolean busy() { return BUSY.get(); }

    static void refreshAsync(Context context, boolean discover) {
        Context app = context.getApplicationContext();
        if (!BUSY.compareAndSet(false, true)) return;
        broadcast(app, "");
        EXECUTOR.execute(() -> {
            String error = "";
            try {
                syncLiveRuns(app);
                RepositoryScanner scanner = new RepositoryScanner(app);
                if (!scanner.rootAvailable()) {
                    error = "Root access is unavailable or was denied";
                } else {
                    if (discover) {
                        for (RepositoryLocation location : scanner.discoverAll()) {
                            addLocation(app, location, false, false);
                        }
                    }
                    List<RepositoryRecord> values = DevtoolDatabase.get(app).repositories();
                    for (RepositoryRecord record : values) {
                        if (!record.saved && record.activeRunCount <= 0 && !discover) continue;
                        persistInspection(app, scanner, record);
                    }
                }
            } catch (RuntimeException failure) {
                error = failure.getMessage() == null ? failure.getClass().getSimpleName() : failure.getMessage();
            } finally {
                BUSY.set(false);
                broadcast(app, error);
            }
        });
    }

    static void addAndInspectAsync(Context context, RepositoryLocation location, boolean saved) {
        Context app = context.getApplicationContext();
        if (!BUSY.compareAndSet(false, true)) return;
        broadcast(app, "");
        EXECUTOR.execute(() -> {
            String error = "";
            try {
                RepositoryRecord record = addLocation(app, location, saved, true);
                RepositoryScanner scanner = new RepositoryScanner(app);
                if (!scanner.rootAvailable()) error = "Root access is unavailable or was denied";
                else persistInspection(app, scanner, record);
            } catch (RuntimeException failure) {
                error = failure.getMessage() == null ? failure.getClass().getSimpleName() : failure.getMessage();
            } finally {
                BUSY.set(false);
                broadcast(app, error);
            }
        });
    }

    private static RepositoryRecord persistInspection(Context context, RepositoryScanner scanner,
                                                      RepositoryRecord record) {
        RepositoryRecord inspected = scanner.inspect(record);
        if (inspected.gitRoot.isEmpty()) {
            DevtoolDatabase.get(context).upsertRepository(inspected);
            return inspected;
        }
        RepositoryLocation canonical;
        if (inspected.environment.equals("chroot")) {
            canonical = RepositoryLocation.chroot(inspected.chrootName, inspected.linuxUser,
                    inspected.gitRoot);
        } else if (inspected.environment.equals("native-termux")) {
            canonical = RepositoryLocation.termux(inspected.gitRoot);
        } else {
            canonical = RepositoryLocation.root(inspected.gitRoot);
        }
        String oldId = inspected.repositoryId;
        inspected.repositoryId = canonical.id();
        inspected.canonicalPath = canonical.path;
        inspected.hostPath = canonical.hostPath;
        DevtoolDatabase.get(context).rekeyRepository(oldId, inspected);
        return DevtoolDatabase.get(context).repository(inspected.repositoryId);
    }

    static void setSaved(Context context, String id, boolean saved) {
        RepositoryRecord record = find(context, id);
        if (record == null) return;
        record.saved = saved;
        record.lastUsedAt = System.currentTimeMillis();
        if (!saved && record.activeRunCount <= 0) DevtoolDatabase.get(context).deleteRepository(id);
        else DevtoolDatabase.get(context).upsertRepository(record);
        broadcast(context, "");
    }

    static void remove(Context context, String id) {
        RepositoryRecord record = find(context, id);
        if (record == null) return;
        if (record.activeRunCount > 0) {
            record.saved = false;
            DevtoolDatabase.get(context).upsertRepository(record);
        } else {
            DevtoolDatabase.get(context).deleteRepository(id);
        }
        broadcast(context, "");
    }

    static void touch(Context context, String id) {
        RepositoryRecord record = find(context, id);
        if (record == null) return;
        record.lastUsedAt = System.currentTimeMillis();
        DevtoolDatabase.get(context).upsertRepository(record);
    }

    static RepositoryRecord addLocation(Context context, RepositoryLocation location,
                                        boolean saved, boolean touch) {
        DevtoolDatabase database = DevtoolDatabase.get(context);
        RepositoryRecord record = database.repository(location.id());
        if (record == null) record = new RepositoryRecord();
        record.repositoryId = location.id();
        record.environment = location.environment;
        record.chrootName = location.chrootName;
        record.linuxUser = location.linuxUser;
        record.configuredPath = location.path;
        record.canonicalPath = location.path;
        record.hostPath = location.hostPath;
        record.saved = record.saved || saved;
        if (touch || record.lastUsedAt == 0L) record.lastUsedAt = System.currentTimeMillis();
        if (record.displayName.isEmpty()) record.displayName = baseName(location.path);
        database.upsertRepository(record);
        return record;
    }

    private static void syncLiveRuns(Context context) {
        DevtoolDatabase database = DevtoolDatabase.get(context);
        Map<String, Integer> activeCounts = new HashMap<>();
        Map<String, RunRecord> latest = new HashMap<>();
        for (RunRecord run : RunStore.activeAll(context)) {
            RepositoryLocation location = RepositoryLocation.fromRun(run);
            run.repositoryId = location.id();
            database.upsertRun(run);
            activeCounts.put(location.id(), activeCounts.getOrDefault(location.id(), 0) + 1);
            latest.put(location.id(), run);
            addLocation(context, location, false, true);
        }
        database.resetActiveRunCounts();
        for (Map.Entry<String, Integer> item : activeCounts.entrySet()) {
            RepositoryRecord record = database.repository(item.getKey());
            if (record == null) continue;
            record.activeRunCount = item.getValue();
            RunRecord run = latest.get(item.getKey());
            if (run != null) {
                record.projectType = run.projectType;
                record.lastUsedAt = Math.max(record.lastUsedAt, run.updatedAtMs);
            }
            database.upsertRepository(record);
        }
    }

    static List<RepositoryRecord> filter(List<RepositoryRecord> values, String query, String filter) {
        String needle = clean(query).toLowerCase(java.util.Locale.ROOT);
        String wanted = clean(filter).toLowerCase(java.util.Locale.ROOT);
        List<RepositoryRecord> result = new ArrayList<>();
        for (RepositoryRecord record : values) {
            if (!wanted.isEmpty() && !wanted.equals("all")) {
                boolean matches;
                switch (wanted) {
                    case "modified": matches = record.status.equals("modified"); break;
                    case "clean": matches = record.status.equals("clean"); break;
                    case "live": matches = record.activeRunCount > 0; break;
                    case "missing": matches = record.status.equals("missing") || record.status.equals("error"); break;
                    case "termux": matches = record.environment.equals("native-termux"); break;
                    case "chroot": matches = record.environment.equals("chroot"); break;
                    case "saved": matches = record.saved; break;
                    default: matches = true;
                }
                if (!matches) continue;
            }
            String haystack = String.join("\n", record.name(), record.canonicalPath,
                    record.branch, record.remote, record.environment, record.chrootName,
                    record.linuxUser, record.projectType).toLowerCase(java.util.Locale.ROOT);
            if (!needle.isEmpty() && !haystack.contains(needle)) continue;
            result.add(record);
        }
        return result;
    }

    private static void broadcast(Context context, String error) {
        context.sendBroadcast(new Intent(ACTION_UPDATED).setPackage(context.getPackageName())
                .putExtra(EXTRA_ERROR, error == null ? "" : error));
    }

    private static String baseName(String path) {
        String clean = RepositoryLocation.clean(path);
        int slash = clean.lastIndexOf('/');
        return slash >= 0 && slash + 1 < clean.length() ? clean.substring(slash + 1) : clean;
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
