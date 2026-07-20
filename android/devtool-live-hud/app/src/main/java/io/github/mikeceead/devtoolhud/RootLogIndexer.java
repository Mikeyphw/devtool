package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.Intent;

import org.json.JSONArray;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Root-backed incremental indexer for authoritative Devtool logs and HUD event streams. */
final class RootLogIndexer {
    static final String ACTION_UPDATED = "io.github.mikeceead.devtoolhud.LOG_INDEX_UPDATED";
    static final String EXTRA_ERROR = "error";
    private static final AtomicBoolean BUSY = new AtomicBoolean(false);
    private static volatile long lastStartedAt;
    private static final int CHUNK_BYTES = 512 * 1024;
    private static final int MAX_CHUNKS_PER_FILE = 8;
    private static final int MAX_FILES_PER_SOURCE = 300;
    private static final Pattern DIAGNOSTIC = Pattern.compile("(?i)\\b([A-Z]{1,6}\\d{3,6})\\b");
    private static final Pattern TEST_TOTALS = Pattern.compile(
            "(?i)(\\d+)\\s+passed.*?(\\d+)\\s+failed.*?(\\d+)\\s+skipped");
    private static final Pattern COUNT_ERRORS = Pattern.compile("(?i)\\b(\\d+)\\s+errors?\\b");
    private static final Pattern COUNT_WARNINGS = Pattern.compile("(?i)\\b(\\d+)\\s+warnings?\\b");

    private RootLogIndexer() {}

    static boolean busy() { return BUSY.get(); }

    static void refreshIfStale(Context context, long minimumAgeMs) {
        if (System.currentTimeMillis() - lastStartedAt >= Math.max(5_000L, minimumAgeMs)) refreshAsync(context);
    }

    static void refreshAsync(Context context) {
        Context app = context.getApplicationContext();
        if (!BUSY.compareAndSet(false, true)) return;
        lastStartedAt = System.currentTimeMillis();
        new Thread(() -> {
            String error = "";
            try {
                new RootLogIndexer.Session(app).scan();
            } catch (Exception failure) {
                error = compact(failure.getMessage(), "Log indexing failed");
            } finally {
                BUSY.set(false);
                app.sendBroadcast(new Intent(ACTION_UPDATED).setPackage(app.getPackageName())
                        .putExtra(EXTRA_ERROR, error));
            }
        }, "devtool-root-log-indexer").start();
    }

    private static final class Session {
        private final Context context;
        private final DevtoolDatabase database;
        private final RootCommandBroker broker;
        private final Set<String> mirroredFingerprints = new HashSet<>();

        Session(Context context) {
            this.context = context;
            this.database = DevtoolDatabase.get(context);
            this.broker = new RootCommandBroker(context);
        }

        void scan() {
            if (!broker.available()) throw new IllegalStateException("Root access is unavailable");
            List<Source> sources = new ArrayList<>();
            sources.add(new Source("termux-xdg", "Termux Devtool", "devtool", RepositoryLocation.termux("~/"),
                    new String[]{"~/.local/state/devtool", "~/.local/state/devtool-android-hud"}, false));
            sources.add(new Source("termux-tasker", "Termux Tasker bridge", "hud", RepositoryLocation.termux("~/"),
                    new String[]{"/sdcard/Tasker/devtool"}, false));
            for (String name : broker.discoverChrootNames()) {
                sources.add(new Source("chroot-" + safeId(name), name + " Devtool", "chroot",
                        RepositoryLocation.chroot(name, "root", "/"),
                        new String[]{"/home", "/root/.local/state/devtool", "/home/termux/.local/state/devtool-android-hud"}, false));
            }
            sources.add(new Source("root-bridge", "Android root bridge", "bridge", RepositoryLocation.root("/"),
                    new String[]{"/data/adb/devtool", "/data/local/tmp/devtool", "/data/data/io.github.mikeceead.devtoolhud/files"}, false));
            // Mirror is deliberately last. Files already indexed from the authoritative Termux source are skipped.
            sources.add(new Source("phone-mirror", "Phone log mirror", "mirror", RepositoryLocation.root("/"),
                    new String[]{"/sdcard/.logs/devtool"}, true));
            for (Source source : sources) scanSource(source);
        }

        private void scanSource(Source source) {
            long now = System.currentTimeMillis();
            RootCommandBroker.CommandResult result = broker.execute(source.location, listCommand(source.roots), 25L);
            String sourceError = result.exitCode == 0 || result.bytes.length > 0 ? "" : compact(result.text(), "Source scan failed");
            database.upsertLogSource(source.id, source.name, source.type, String.join(";", source.roots),
                    source.location.environment, now, sourceError);
            if (result.bytes.length == 0) return;
            int seen = 0;
            for (FileStat file : parseFileStats(result.bytes)) {
                if (++seen > MAX_FILES_PER_SOURCE) break;
                String fingerprint = file.basename() + "\u001f" + file.size + "\u001f" + file.modifiedAt;
                if (source.mirror && mirroredFingerprints.contains(fingerprint)) continue;
                if (file.path.endsWith(".log")) mirroredFingerprints.add(fingerprint);
                scanFile(source, file);
            }
        }

        private void scanFile(Source source, FileStat file) {
            DevtoolDatabase.FileCursor cursor = database.fileCursor(source.id, file.path);
            if (cursor == null) cursor = new DevtoolDatabase.FileCursor();
            boolean reset = !file.inode.equals(cursor.inode) || file.size < cursor.byteOffset;
            if (reset) {
                cursor.byteOffset = 0L;
                cursor.partialLine = "";
            }
            cursor.sourceId = source.id;
            cursor.path = file.path;
            cursor.inode = file.inode;
            cursor.sizeBytes = file.size;
            cursor.modifiedAt = file.modifiedAt;
            cursor.parserType = file.path.endsWith("events.jsonl") ? "jsonl" : "text";
            int chunks = 0;
            while (cursor.byteOffset < file.size && chunks++ < MAX_CHUNKS_PER_FILE) {
                int count = (int) Math.min(CHUNK_BYTES, file.size - cursor.byteOffset);
                RootCommandBroker.CommandResult read = broker.execute(source.location,
                        readCommand(file.path, cursor.byteOffset, count), 20L);
                if (read.exitCode != 0 || read.bytes.length == 0) break;
                long chunkStart = cursor.byteOffset;
                cursor.byteOffset += read.bytes.length;
                ingestChunk(source, file, cursor, chunkStart, read.bytes);
            }
            cursor.lastScanAt = System.currentTimeMillis();
            database.upsertFileCursor(cursor);
        }

        private void ingestChunk(Source source, FileStat file, DevtoolDatabase.FileCursor cursor,
                                 long chunkStart, byte[] bytes) {
            String text = cursor.partialLine + new String(bytes, StandardCharsets.UTF_8);
            String[] lines = text.split("\\n", -1);
            boolean complete = text.endsWith("\n");
            int limit = complete ? lines.length : lines.length - 1;
            long approximateOffset = Math.max(0L, chunkStart - cursor.partialLine.getBytes(StandardCharsets.UTF_8).length);
            for (int i = 0; i < limit; i++) {
                String line = stripCarriageReturn(lines[i]);
                long sequence = approximateOffset;
                approximateOffset += line.getBytes(StandardCharsets.UTF_8).length + 1L;
                if (line.trim().isEmpty()) continue;
                if (cursor.parserType.equals("jsonl")) ingestJsonEvent(source, file, line, sequence);
                else ingestTextLine(source, file, line, sequence);
            }
            cursor.partialLine = complete ? "" : lines[lines.length - 1];
            if (cursor.partialLine.length() > 256_000) {
                ingestTextLine(source, file, cursor.partialLine, approximateOffset);
                cursor.partialLine = "";
            }
        }

        private void ingestJsonEvent(Source source, FileStat file, String line, long offset) {
            try {
                JSONObject event = new JSONObject(line);
                String runId = clean(event.optString("run_id", ""));
                if (runId.isEmpty()) runId = externalRunId(source.id, file.path, file.inode);
                long sequence = event.optLong("sequence", event.optLong("seq", offset + 1L));
                if (database.hasIndexedLogEntry(runId, source.id, file.path, sequence)) return;
                JSONObject phaseObject = event.optJSONObject("phase");
                JSONObject result = event.optJSONObject("result");
                JSONObject progress = event.optJSONObject("progress");
                String eventType = first(event.optString("event_type", ""), event.optString("type", ""), "event");
                String phase = phaseObject == null ? clean(event.optString("phase", ""))
                        : first(phaseObject.optString("display_name", ""), phaseObject.optString("name", ""));
                String message = first(event.optString("message", ""), event.optString("detail", ""),
                        event.optString("text", ""), progress == null ? "" : progress.optString("detail", ""), eventType);
                String status = normalizeStatus(first(event.optString("status", ""),
                        result == null ? "" : result.optString("status", "")));
                String severity = severityFor(message, status, eventType);
                long timestamp = eventTimestamp(event, file.modifiedAt);
                String tool = toolFor(phase, message);
                String code = diagnosticCode(message);

                RunRecord run = database.findRun(runId);
                if (run == null) run = new RunRecord();
                run.runId = runId;
                run.logPath = first(event.optString("log_path", ""), result == null ? "" : result.optString("log_path", ""), file.path);
                run.logSourceId = source.id;
                run.title = first(event.optString("title", ""), event.optString("repo_name", ""), basename(run.logPath), "Devtool run");
                run.phase = first(phase, run.phase, "Indexed log");
                run.detail = message;
                run.status = mergeStatus(run.status, status, severity);
                run.repo = first(event.optString("repo_root", ""), event.optString("repo", ""), run.repo);
                run.workdir = first(event.optString("workdir", ""), run.repo, run.workdir);
                run.projectType = firstNonAuto(event.optString("project_type", ""), run.projectType);
                run.executionEnvironment = firstNonAuto(event.optString("execution_environment", ""),
                        source.location.environment, run.executionEnvironment);
                run.target = first(event.optString("target", ""), run.target);
                run.command = first(event.optString("command", ""), run.command);
                run.summary = first(event.optString("summary", ""), run.summary);
                run.artifact = first(event.optString("artifact", ""), result == null ? "" : result.optString("artifact", ""), run.artifact);
                run.commitHash = first(event.optString("commit", ""), event.optString("commit_hash", ""),
                        result == null ? "" : result.optString("commit", ""), run.commitHash);
                run.transactionBackup = first(event.optString("transaction_backup", ""), run.transactionBackup);
                run.startedAtMs = Math.min(run.startedAtMs, eventStartTimestamp(event, timestamp));
                run.updatedAtMs = Math.max(run.updatedAtMs, timestamp);
                run.lastProgressAtMs = run.updatedAtMs;
                run.durationMs = Math.max(run.durationMs, event.optLong("duration_ms", result == null ? 0L : result.optLong("duration_ms", 0L)));
                run.warnings = Math.max(run.warnings, event.optInt("warnings", result == null ? 0 : result.optInt("warnings", 0)));
                run.errors = Math.max(run.errors, event.optInt("errors", result == null ? 0 : result.optInt("errors", 0)));
                if (severity.equals("warning")) run.warnings = Math.max(1, run.warnings);
                if (severity.equals("error") || severity.equals("fatal")) run.errors = Math.max(1, run.errors);
                if (progress != null) {
                    run.current = progress.optInt("current", progress.optInt("completed", run.current));
                    run.total = progress.optInt("total", run.total);
                    run.progress = progress.optInt("percent", event.optInt("percent", run.progress));
                } else {
                    run.current = event.optInt("current", run.current);
                    run.total = event.optInt("total", run.total);
                    run.progress = event.optInt("percent", run.progress);
                }
                run.indeterminate = run.total <= 0 && !run.terminal();
                linkRepository(run);
                database.upsertRun(run);
                database.addIndexedLogEntry(runId, sequence, timestamp, severity, categoryFor(eventType, phase),
                        source.id, phase, tool, "event", message, line, file.path, -1, code, line);
            } catch (Exception malformed) {
                ingestTextLine(source, file, line, offset);
            }
        }

        private void ingestTextLine(Source source, FileStat file, String line, long sequence) {
            String runId = externalRunId(source.id, file.path, file.inode);
            String severity = classifySeverity(line);
            String phase = classifyPhase(line);
            String code = diagnosticCode(line);
            long timestamp = file.modifiedAt > 0 ? file.modifiedAt : System.currentTimeMillis();
            RunRecord run = database.findRun(runId);
            if (run == null) {
                run = new RunRecord();
                run.runId = runId;
                run.title = basename(file.path);
                run.status = "waiting";
                run.phase = "Indexed log";
                run.startedAtMs = timestamp;
                run.executionEnvironment = source.location.environment;
                run.projectType = "auto";
            }
            run.logPath = file.path;
            run.logSourceId = source.id;
            run.updatedAtMs = Math.max(run.updatedAtMs, timestamp);
            run.detail = compact(line, run.detail);
            run.phase = first(phase, run.phase);
            run.status = mergeStatus(run.status, "", severity);
            if (severity.equals("warning")) run.warnings++;
            if (severity.equals("error") || severity.equals("fatal")) run.errors++;
            parseTextMetadata(run, line);
            linkRepository(run);
            database.upsertRun(run);
            database.addIndexedLogEntry(runId, sequence + 1L, timestamp, severity, categoryFor("output", phase),
                    source.id, phase, toolFor(phase, line), "stdout", compact(line, line), line,
                    file.path, -1, code, "");
        }

        private void linkRepository(RunRecord run) {
            if (clean(run.repo).isEmpty() && clean(run.workdir).isEmpty()) return;
            RepositoryLocation location = RepositoryLocation.fromRun(run);
            run.repositoryId = location.id();
            RepositoryRecord repository = database.repository(run.repositoryId);
            if (repository == null) repository = new RepositoryRecord();
            repository.repositoryId = location.id();
            repository.environment = location.environment;
            repository.chrootName = location.chrootName;
            repository.linuxUser = location.linuxUser;
            repository.configuredPath = location.path;
            repository.canonicalPath = location.path;
            repository.hostPath = location.hostPath;
            repository.projectType = run.projectType;
            repository.lastUsedAt = Math.max(repository.lastUsedAt, run.updatedAtMs);
            if (repository.displayName.isEmpty()) repository.displayName = basename(location.path);
            database.upsertRepository(repository);
        }
    }

    static String classifySeverity(String text) {
        String clean = clean(text).toLowerCase(Locale.ROOT);
        if (clean.isEmpty()) return "info";
        if (clean.matches(".*\\b0\\s+(errors?|failed)\\b.*") && !clean.matches(".*\\b[1-9]\\d*\\s+(errors?|failed)\\b.*")) {
            if (clean.contains("warning") && !clean.matches(".*\\b0\\s+warnings?\\b.*")) return "warning";
            return clean.contains("passed") || clean.contains("success") ? "success" : "info";
        }
        if (clean.contains("fatal") || clean.contains("traceback") || clean.contains("exception")) return "fatal";
        if (clean.matches(".*\\b(error|errors|failed|failure|not ok)\\b.*") || clean.startsWith("e: ")) return "error";
        if (clean.matches(".*\\b(warn|warning|warnings|deprecated)\\b.*") || clean.startsWith("w: ")) return "warning";
        if (clean.contains("completed successfully") || clean.contains("validation passed")
                || clean.matches(".*\\bpassed\\b.*") || clean.matches(".*\\bsucceeded\\b.*")) return "success";
        return "info";
    }

    static String classifyPhase(String text) {
        String clean = clean(text).toLowerCase(Locale.ROOT);
        if (clean.contains("rollback")) return "Rollback";
        if (clean.contains("restore")) return "Restore";
        if (clean.contains("extract") || clean.contains("overlay")) return "Apply";
        if (clean.contains("test") || clean.contains("passed") || clean.contains("skipped")) return "Test";
        if (clean.contains("build") || clean.contains("compile") || clean.contains("gradle")) return "Build";
        if (clean.contains("validat") || clean.contains("diagnostic")) return "Validation";
        if (clean.contains("commit") || clean.contains("git ")) return "Git";
        if (clean.contains("artifact")) return "Artifact";
        return "";
    }

    private static void parseTextMetadata(RunRecord run, String line) {
        String clean = clean(line);
        String lower = clean.toLowerCase(Locale.ROOT);
        if (clean.startsWith("$ ")) run.command = clean.substring(2).trim();
        if (lower.startsWith("transaction backup:")) run.transactionBackup = afterColon(clean);
        if (lower.startsWith("git commit:")) run.commitHash = afterColon(clean);
        if (lower.startsWith("artifact:")) run.artifact = afterColon(clean);
        if (lower.startsWith("applied overlay:")) run.title = clean;
        if (lower.contains("devtool completed successfully")) {
            run.status = "ok"; run.progress = 100; run.indeterminate = false; run.exitCode = 0;
        } else if (lower.contains("devtool failed") || lower.startsWith("failure details:")) {
            run.status = "failed"; run.indeterminate = false; run.exitCode = 1;
        }
        Matcher tests = TEST_TOTALS.matcher(clean);
        if (tests.find()) run.errors = Math.max(run.errors, Integer.parseInt(tests.group(2)));
        Matcher errors = COUNT_ERRORS.matcher(clean);
        if (errors.find()) run.errors = Math.max(run.errors, Integer.parseInt(errors.group(1)));
        Matcher warnings = COUNT_WARNINGS.matcher(clean);
        if (warnings.find()) run.warnings = Math.max(run.warnings, Integer.parseInt(warnings.group(1)));
        if (!run.command.isEmpty()) {
            RepositoryLocation location = RepositoryLocation.fromRun(run);
            if (run.repo.isEmpty()) run.repo = location.path;
            run.workdir = run.repo;
        }
    }

    private static String listCommand(String[] roots) {
        StringBuilder command = new StringBuilder("for root in");
        for (String root : roots) command.append(' ').append(RootCommandBroker.shellQuote(root));
        command.append("; do case \"$root\" in \"~/\"*) root=\"$HOME/${root#??}\";; esac; ")
                .append("[ -d \"$root\" ] || continue; ")
                .append("find \"$root\" -xdev -maxdepth 8 -type f \\( ")
                .append("-path '*/.local/state/devtool/logs/*.log' -o ")
                .append("-path '*/.local/state/devtool/events.jsonl' -o ")
                .append("-path '*/.local/state/devtool-android-hud/events.jsonl' -o ")
                .append("-path '*/Tasker/devtool/events.jsonl' -o ")
                .append("-path '*/.logs/devtool/*.log' -o ")
                .append("-path '/data/adb/devtool/*.log' -o ")
                .append("-path '/data/local/tmp/devtool/*.log' -o ")
                .append("-path '/data/data/io.github.mikeceead.devtoolhud*/files/*.log' -o ")
                .append("-name 'android-hud-bridge-error.log' \\) ")
                .append("-printf '%i\\t%s\\t%T@\\t%p\\0' 2>/dev/null; done");
        return command.toString();
    }

    private static String readCommand(String path, long offset, int count) {
        return "dd if=" + RootCommandBroker.shellQuote(path) + " bs=1 skip=" + offset
                + " count=" + count + " status=none 2>/dev/null";
    }

    private static List<FileStat> parseFileStats(byte[] bytes) {
        List<FileStat> files = new ArrayList<>();
        int start = 0;
        for (int i = 0; i <= bytes.length; i++) {
            if (i != bytes.length && bytes[i] != 0) continue;
            if (i > start) {
                String record = new String(bytes, start, i - start, StandardCharsets.UTF_8);
                String[] fields = record.split("\\t", 4);
                if (fields.length == 4) {
                    FileStat value = new FileStat();
                    value.inode = fields[0];
                    value.size = parseLong(fields[1], 0L);
                    double seconds = parseDouble(fields[2], 0d);
                    value.modifiedAt = (long) (seconds * 1000d);
                    value.path = fields[3];
                    files.add(value);
                }
            }
            start = i + 1;
        }
        return files;
    }

    private static long eventTimestamp(JSONObject event, long fallback) {
        Object value = event.opt("updated_at");
        if (value == null) value = event.opt("timestamp");
        if (value == null) value = event.opt("created_at");
        return timestampValue(value, fallback > 0 ? fallback : System.currentTimeMillis());
    }

    private static long eventStartTimestamp(JSONObject event, long fallback) {
        Object value = event.opt("started_at");
        return timestampValue(value, fallback);
    }

    private static long timestampValue(Object value, long fallback) {
        if (value instanceof Number) {
            double number = ((Number) value).doubleValue();
            return number < 10_000_000_000d ? (long) (number * 1000d) : (long) number;
        }
        String clean = value == null ? "" : String.valueOf(value).trim();
        if (clean.isEmpty()) return fallback;
        try { return Instant.parse(clean).toEpochMilli(); }
        catch (DateTimeParseException ignored) { return parseLong(clean, fallback); }
    }

    private static String severityFor(String message, String status, String eventType) {
        if (status.equals("failed")) return "error";
        if (status.equals("ok") && (eventType.contains("finished") || eventType.contains("completed"))) return "success";
        return classifySeverity(message);
    }

    private static String mergeStatus(String current, String requested, String severity) {
        String old = normalizeStatus(current);
        String next = normalizeStatus(requested);
        if (old.equals("failed") || old.equals("cancelled") || old.equals("interrupted")) return old;
        if (next.equals("failed") || next.equals("cancelled") || next.equals("interrupted")) return next;
        if (severity.equals("error") || severity.equals("fatal")) return "failed";
        if (next.equals("ok")) return "ok";
        if (old.equals("ok")) return old;
        return next.isEmpty() || next.equals("waiting") ? (old.isEmpty() ? "waiting" : old) : next;
    }

    private static String normalizeStatus(String value) {
        String clean = clean(value).toLowerCase(Locale.ROOT).replace('-', '_');
        if (clean.equals("success") || clean.equals("succeeded") || clean.equals("complete") || clean.equals("completed")) return "ok";
        if (clean.equals("error") || clean.equals("failure")) return "failed";
        if (clean.equals("running") || clean.equals("waiting") || clean.equals("ok") || clean.equals("failed")
                || clean.equals("cancelled") || clean.equals("interrupted") || clean.equals("skipped")) return clean;
        return "";
    }

    private static String categoryFor(String eventType, String phase) {
        String clean = (eventType + " " + phase).toLowerCase(Locale.ROOT);
        if (clean.contains("git") || clean.contains("commit")) return "git";
        if (clean.contains("rollback")) return "rollback";
        if (clean.contains("test")) return "test";
        if (clean.contains("build") || clean.contains("compile")) return "build";
        if (clean.contains("validat") || clean.contains("diagnostic")) return "validation";
        if (clean.contains("artifact") || clean.contains("overlay") || clean.contains("extract")) return "artifact";
        return "execution";
    }

    private static String toolFor(String phase, String message) {
        String clean = (phase + " " + message).toLowerCase(Locale.ROOT);
        if (clean.contains("gradle")) return "gradle";
        if (clean.contains("dotnet") || DIAGNOSTIC.matcher(message).find()) return "dotnet";
        if (clean.contains("pytest") || clean.contains("traceback")) return "python";
        if (clean.contains("cmake")) return "cmake";
        if (clean.contains("git")) return "git";
        return "devtool";
    }

    private static String diagnosticCode(String message) {
        Matcher matcher = DIAGNOSTIC.matcher(message == null ? "" : message);
        return matcher.find() ? matcher.group(1) : "";
    }

    private static String externalRunId(String source, String path, String inode) {
        return "external-" + sha256(source + "\u001f" + path + "\u001f" + inode).substring(0, 24);
    }

    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder output = new StringBuilder();
            for (byte item : digest) output.append(String.format(Locale.ROOT, "%02x", item));
            return output.toString();
        } catch (Exception ignored) { return Integer.toHexString(value.hashCode()) + "000000000000000000000000"; }
    }

    private static String safeId(String value) { return clean(value).replaceAll("[^A-Za-z0-9._-]", "_"); }
    private static String afterColon(String value) { int index = value.indexOf(':'); return index < 0 ? "" : value.substring(index + 1).trim(); }
    private static String basename(String path) { String clean = clean(path); int slash = clean.lastIndexOf('/'); return slash < 0 ? clean : clean.substring(slash + 1); }
    private static String stripCarriageReturn(String value) { return value.endsWith("\r") ? value.substring(0, value.length() - 1) : value; }
    private static String first(String... values) { for (String value : values) if (!clean(value).isEmpty()) return clean(value); return ""; }
    private static String firstNonAuto(String... values) { for (String value : values) { String clean = clean(value); if (!clean.isEmpty() && !clean.equalsIgnoreCase("auto")) return clean; } return "auto"; }
    private static String clean(String value) { return value == null ? "" : HudUpdate.plainText(value).trim(); }
    private static String compact(String value, String fallback) { String clean = clean(value).replace('\n', ' ').replace('\r', ' '); if (clean.isEmpty()) clean = clean(fallback); return clean.length() > 500 ? clean.substring(0, 497) + "…" : clean; }
    private static long parseLong(String value, long fallback) { try { return Long.parseLong(value.trim()); } catch (Exception ignored) { return fallback; } }
    private static double parseDouble(String value, double fallback) { try { return Double.parseDouble(value.trim()); } catch (Exception ignored) { return fallback; } }

    private static final class Source {
        final String id, name, type;
        final RepositoryLocation location;
        final String[] roots;
        final boolean mirror;
        Source(String id, String name, String type, RepositoryLocation location, String[] roots, boolean mirror) {
            this.id = id; this.name = name; this.type = type; this.location = location; this.roots = roots; this.mirror = mirror;
        }
    }

    private static final class FileStat {
        String inode = "";
        long size;
        long modifiedAt;
        String path = "";
        String basename() { return RootLogIndexer.basename(path); }
    }
}
