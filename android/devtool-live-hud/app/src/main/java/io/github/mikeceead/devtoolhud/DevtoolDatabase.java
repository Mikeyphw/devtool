package io.github.mikeceead.devtoolhud;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

import java.io.File;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

final class DevtoolDatabase extends SQLiteOpenHelper {
    private static final String NAME = "devtool_android.db";
    private static final int VERSION = 9;

    static int schemaVersion() { return VERSION; }
    private static volatile DevtoolDatabase instance;

    static DevtoolDatabase get(Context context) {
        if (instance == null) {
            synchronized (DevtoolDatabase.class) {
                if (instance == null) instance = new DevtoolDatabase(context.getApplicationContext());
            }
        }
        return instance;
    }

    private DevtoolDatabase(Context context) {
        super(context, NAME, null, VERSION);
        setWriteAheadLoggingEnabled(true);
    }

    @Override
    public void onConfigure(SQLiteDatabase db) {
        super.onConfigure(db);
        db.setForeignKeyConstraintsEnabled(true);
    }

    @Override
    public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE runs ("
                + "run_id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,"
                + "detail TEXT NOT NULL, repo TEXT NOT NULL, repository_id TEXT NOT NULL DEFAULT '',"
                + "project_type TEXT NOT NULL, execution_environment TEXT NOT NULL, workdir TEXT NOT NULL,"
                + "target TEXT NOT NULL, command TEXT NOT NULL, summary TEXT NOT NULL, stdout TEXT NOT NULL,"
                + "stderr TEXT NOT NULL, error TEXT NOT NULL, launch_log TEXT NOT NULL, launch_mode TEXT NOT NULL,"
                + "last_progress_at_ms INTEGER NOT NULL, progress INTEGER NOT NULL, current_value INTEGER NOT NULL,"
                + "total_value INTEGER NOT NULL, unit_label TEXT NOT NULL, warnings INTEGER NOT NULL, errors INTEGER NOT NULL,"
                + "steps_json TEXT NOT NULL, indeterminate INTEGER NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,"
                + "started_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL, duration_ms INTEGER NOT NULL,"
                + "artifact TEXT NOT NULL DEFAULT '',"
                + "commit_hash TEXT NOT NULL DEFAULT '', transaction_backup TEXT NOT NULL DEFAULT '',"
                + "log_path TEXT NOT NULL DEFAULT '', log_source_id TEXT NOT NULL DEFAULT '', exit_code INTEGER NOT NULL DEFAULT -1)");
        db.execSQL("CREATE INDEX runs_updated_idx ON runs(updated_at_ms DESC)");
        db.execSQL("CREATE INDEX runs_repository_idx ON runs(repository_id, updated_at_ms DESC)");
        db.execSQL("CREATE INDEX runs_retention_idx ON runs(pinned, status, updated_at_ms DESC)");

        db.execSQL("CREATE TABLE repositories ("
                + "repository_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, environment TEXT NOT NULL,"
                + "chroot_name TEXT NOT NULL, linux_user TEXT NOT NULL, configured_path TEXT NOT NULL,"
                + "canonical_path TEXT NOT NULL, host_path TEXT NOT NULL, git_root TEXT NOT NULL, branch TEXT NOT NULL,"
                + "upstream TEXT NOT NULL, remote TEXT NOT NULL, status TEXT NOT NULL, dirty INTEGER NOT NULL,"
                + "ahead_count INTEGER NOT NULL, behind_count INTEGER NOT NULL, active_run_count INTEGER NOT NULL,"
                + "saved INTEGER NOT NULL, last_used_at INTEGER NOT NULL, last_scanned_at INTEGER NOT NULL,"
                + "last_error TEXT NOT NULL, project_type TEXT NOT NULL)");
        db.execSQL("CREATE INDEX repositories_saved_idx ON repositories(saved DESC, last_used_at DESC)");
        db.execSQL("CREATE UNIQUE INDEX repositories_location_idx ON repositories(environment, chroot_name, linux_user, canonical_path)");

        db.execSQL("CREATE TABLE repository_events ("
                + "event_id INTEGER PRIMARY KEY AUTOINCREMENT, repository_id TEXT NOT NULL, run_id TEXT NOT NULL DEFAULT '',"
                + "event_type TEXT NOT NULL, summary TEXT NOT NULL, created_at INTEGER NOT NULL,"
                + "FOREIGN KEY(repository_id) REFERENCES repositories(repository_id) ON DELETE CASCADE)");
        db.execSQL("CREATE INDEX repository_events_repo_idx ON repository_events(repository_id, created_at DESC)");

        db.execSQL("CREATE TABLE repository_operations ("
                + "operation_id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, run_id TEXT NOT NULL,"
                + "operation_type TEXT NOT NULL, target TEXT NOT NULL, state TEXT NOT NULL,"
                + "progress_label TEXT NOT NULL, summary TEXT NOT NULL, queued_at INTEGER NOT NULL,"
                + "queue_order INTEGER NOT NULL DEFAULT 0, started_at INTEGER NOT NULL DEFAULT 0,"
                + "finished_at INTEGER NOT NULL DEFAULT 0,"
                + "exit_code INTEGER NOT NULL DEFAULT -1, cancellable INTEGER NOT NULL DEFAULT 1,"
                + "FOREIGN KEY(repository_id) REFERENCES repositories(repository_id) ON DELETE CASCADE)");
        db.execSQL("CREATE INDEX repository_operations_repo_idx ON repository_operations(repository_id, queued_at DESC)");
        db.execSQL("CREATE INDEX repository_operations_state_idx ON repository_operations(state, queued_at)");
        db.execSQL("CREATE INDEX repository_operations_retention_idx ON repository_operations(state, finished_at)");

        db.execSQL("CREATE TABLE log_entries ("
                + "entry_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, sequence_value INTEGER NOT NULL DEFAULT 0,"
                + "timestamp_ms INTEGER NOT NULL, severity TEXT NOT NULL, category TEXT NOT NULL, source TEXT NOT NULL,"
                + "phase TEXT NOT NULL, tool TEXT NOT NULL, stream TEXT NOT NULL, message TEXT NOT NULL, raw_text TEXT NOT NULL,"
                + "file_path TEXT NOT NULL, line_number INTEGER NOT NULL DEFAULT -1, diagnostic_code TEXT NOT NULL DEFAULT '',"
                + "metadata_json TEXT NOT NULL DEFAULT '')");
        db.execSQL("CREATE INDEX log_entries_run_idx ON log_entries(run_id, timestamp_ms, entry_id)");
        db.execSQL("CREATE VIRTUAL TABLE log_entries_fts USING fts4(run_id, message, raw_text, file_path, diagnostic_code, tool)");

        db.execSQL("CREATE TABLE log_sources (source_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, source_type TEXT NOT NULL,"
                + "root_path TEXT NOT NULL, environment TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, last_scan_at INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '')");
        db.execSQL("CREATE TABLE file_cursors (source_id TEXT NOT NULL, path TEXT NOT NULL, inode TEXT NOT NULL DEFAULT '',"
                + "byte_offset INTEGER NOT NULL DEFAULT 0, size_bytes INTEGER NOT NULL DEFAULT 0, modified_at INTEGER NOT NULL DEFAULT 0,"
                + "parser_type TEXT NOT NULL DEFAULT 'text', partial_line TEXT NOT NULL DEFAULT '', last_scan_at INTEGER NOT NULL DEFAULT 0,"
                + "PRIMARY KEY(source_id, path))");

        createValidationTables(db);
    }

    @Override
    public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        // This database first ships with the structured Logs/Repositories slice. Recreate only
        // internal indexes/tables when upgrading an unreleased development database.
        if (oldVersion < 3) {
            db.execSQL("DROP TABLE IF EXISTS log_entries_fts");
            db.execSQL("DROP TABLE IF EXISTS log_entries");
            db.execSQL("DROP TABLE IF EXISTS repository_events");
            db.execSQL("DROP TABLE IF EXISTS repositories");
            db.execSQL("DROP TABLE IF EXISTS runs");
            db.execSQL("DROP TABLE IF EXISTS log_sources");
            db.execSQL("DROP TABLE IF EXISTS file_cursors");
            onCreate(db);
            return;
        }
        if (oldVersion < 4) {
            db.execSQL("ALTER TABLE runs ADD COLUMN log_path TEXT NOT NULL DEFAULT ''");
            db.execSQL("ALTER TABLE runs ADD COLUMN log_source_id TEXT NOT NULL DEFAULT ''");
        }
        if (oldVersion < 5) {
            db.execSQL("CREATE TABLE IF NOT EXISTS repository_operations ("
                    + "operation_id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, run_id TEXT NOT NULL,"
                    + "operation_type TEXT NOT NULL, target TEXT NOT NULL, state TEXT NOT NULL,"
                    + "progress_label TEXT NOT NULL, summary TEXT NOT NULL, queued_at INTEGER NOT NULL,"
                    + "queue_order INTEGER NOT NULL DEFAULT 0, started_at INTEGER NOT NULL DEFAULT 0,"
                    + "finished_at INTEGER NOT NULL DEFAULT 0,"
                    + "exit_code INTEGER NOT NULL DEFAULT -1, cancellable INTEGER NOT NULL DEFAULT 1,"
                    + "FOREIGN KEY(repository_id) REFERENCES repositories(repository_id) ON DELETE CASCADE)");
            db.execSQL("CREATE INDEX IF NOT EXISTS repository_operations_repo_idx ON repository_operations(repository_id, queued_at DESC)");
            db.execSQL("CREATE INDEX IF NOT EXISTS repository_operations_state_idx ON repository_operations(state, queued_at)");
        }
        if (oldVersion < 6) {
            db.execSQL("ALTER TABLE runs ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0");
            if (oldVersion >= 5) {
                db.execSQL("ALTER TABLE repository_operations ADD COLUMN queue_order INTEGER NOT NULL DEFAULT 0");
            }
            db.execSQL("UPDATE repository_operations SET queue_order=queued_at WHERE queue_order=0");
            db.execSQL("CREATE INDEX IF NOT EXISTS runs_retention_idx ON runs(pinned, status, updated_at_ms DESC)");
            db.execSQL("CREATE INDEX IF NOT EXISTS repository_operations_retention_idx ON repository_operations(state, finished_at)");
        }
        if (oldVersion < 7) {
            createValidationTables(db);
        }
        if (oldVersion < 8) {
            addColumn(db, "validation_sessions", "executor_kind TEXT NOT NULL DEFAULT 'native-termux'");
            addColumn(db, "validation_sessions", "executor_name TEXT NOT NULL DEFAULT ''");
            addColumn(db, "validation_sessions", "executor_transport TEXT NOT NULL DEFAULT 'termux'");
            addColumn(db, "validation_sessions", "executor_workdir TEXT NOT NULL DEFAULT ''");
            addColumn(db, "validation_sessions", "executor_linux_user TEXT NOT NULL DEFAULT 'termux'");
            addColumn(db, "validation_sessions", "rollback_status TEXT NOT NULL DEFAULT ''");
            addColumn(db, "validation_sessions", "rollback_error TEXT NOT NULL DEFAULT ''");
            addColumn(db, "validation_sessions", "rollback_total_paths INTEGER NOT NULL DEFAULT 0");
            addColumn(db, "validation_sessions", "rollback_verified_paths INTEGER NOT NULL DEFAULT 0");
            addColumn(db, "validation_sessions", "rollback_backup_healthy INTEGER NOT NULL DEFAULT 0");
            addColumn(db, "validation_sessions", "uncommitted_changes INTEGER NOT NULL DEFAULT 0");
            addColumn(db, "validation_phases", "progress_detail TEXT NOT NULL DEFAULT ''");
            addColumn(db, "validation_phases", "progress_completed INTEGER NOT NULL DEFAULT -1");
            addColumn(db, "validation_phases", "progress_total_value INTEGER NOT NULL DEFAULT -1");
            addColumn(db, "validation_phases", "progress_unit TEXT NOT NULL DEFAULT ''");
            addColumn(db, "validation_phases", "progress_updated_at TEXT NOT NULL DEFAULT ''");
            addColumn(db, "validation_phases", "workspace TEXT NOT NULL DEFAULT ''");
            addColumn(db, "validation_phases", "command_json TEXT NOT NULL DEFAULT '[]'");
            addColumn(db, "validation_phases", "signal INTEGER NOT NULL DEFAULT 0");
            addColumn(db, "validation_phases", "issues_json TEXT NOT NULL DEFAULT '[]'");
            addColumn(db, "validation_phases", "classification_evidence_json TEXT NOT NULL DEFAULT '[]'");
            addColumn(db, "validation_phases", "supervisor_events_json TEXT NOT NULL DEFAULT '[]'");
            addColumn(db, "validation_phases", "input_snapshot_before_json TEXT NOT NULL DEFAULT '{}'");
            addColumn(db, "validation_phases", "input_snapshot_after_json TEXT NOT NULL DEFAULT '{}'");
            addColumn(db, "validation_attempts", "session_revision INTEGER NOT NULL DEFAULT 0");
            addColumn(db, "validation_attempts", "signal INTEGER NOT NULL DEFAULT 0");
            addColumn(db, "validation_attempts", "argv_json TEXT NOT NULL DEFAULT '[]'");
            addColumn(db, "validation_attempts", "issues_json TEXT NOT NULL DEFAULT '[]'");
            addColumn(db, "validation_attempts", "input_snapshot_before_json TEXT NOT NULL DEFAULT '{}'");
            addColumn(db, "validation_attempts", "input_snapshot_after_json TEXT NOT NULL DEFAULT '{}'");
            addColumn(db, "validation_attempts", "classification_evidence_json TEXT NOT NULL DEFAULT '[]'");
            addColumn(db, "validation_attempts", "supervisor_events_json TEXT NOT NULL DEFAULT '[]'");
            addColumn(db, "validation_attempts", "resource_profile TEXT NOT NULL DEFAULT ''");
        }
        if (oldVersion < 9) {
            addColumn(db, "validation_sessions", "report_path TEXT NOT NULL DEFAULT ''");
            addColumn(db, "validation_sessions", "report_generated_at TEXT NOT NULL DEFAULT ''");
            addColumn(db, "validation_sessions", "final_verdict TEXT NOT NULL DEFAULT ''");
            addColumn(db, "validation_sessions", "commit_status TEXT NOT NULL DEFAULT 'not-requested'");
            addColumn(db, "validation_sessions", "commit_hash TEXT NOT NULL DEFAULT ''");
            addColumn(db, "validation_sessions", "initial_git_head TEXT NOT NULL DEFAULT ''");
            addColumn(db, "validation_sessions", "final_git_head TEXT NOT NULL DEFAULT ''");
            addColumn(db, "validation_sessions", "report_warnings INTEGER NOT NULL DEFAULT 0");
            addColumn(db, "validation_sessions", "report_errors INTEGER NOT NULL DEFAULT 0");
        }
    }

    private static void addColumn(SQLiteDatabase db, String table, String declaration) {
        try { db.execSQL("ALTER TABLE " + table + " ADD COLUMN " + declaration); }
        catch (android.database.SQLException ignored) {}
    }

    private static void createValidationTables(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE IF NOT EXISTS validation_sessions ("
                + "session_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL, effective_state TEXT NOT NULL,"
                + "repo_root TEXT NOT NULL, project_root TEXT NOT NULL, repository_id TEXT NOT NULL DEFAULT '', project_type TEXT NOT NULL DEFAULT 'auto',"
                + "target TEXT NOT NULL DEFAULT '', variant TEXT NOT NULL DEFAULT '', artifact TEXT NOT NULL DEFAULT '', validation_mode TEXT NOT NULL DEFAULT 'standalone',"
                + "execution_environment TEXT NOT NULL DEFAULT 'native-termux', failure_action TEXT NOT NULL DEFAULT 'pause', resource_profile TEXT NOT NULL DEFAULT 'automatic',"
                + "required_checkpoint_name TEXT NOT NULL DEFAULT 'finalize', recommended_retry_profile TEXT NOT NULL DEFAULT '', transaction_path TEXT NOT NULL DEFAULT '',"
                + "paused_reason TEXT NOT NULL DEFAULT '', invalidated_from TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '', last_message TEXT NOT NULL DEFAULT '',"
                + "last_reason TEXT NOT NULL DEFAULT '', latest_event_id TEXT NOT NULL DEFAULT '', scope_mode TEXT NOT NULL DEFAULT 'next', scope_through TEXT NOT NULL DEFAULT '',"
                + "scope_selected_json TEXT NOT NULL DEFAULT '[]', created_at_ms INTEGER NOT NULL DEFAULT 0, updated_at_ms INTEGER NOT NULL DEFAULT 0,"
                + "recovery_available INTEGER NOT NULL DEFAULT 0, required_checkpoint_satisfied INTEGER NOT NULL DEFAULT 0,"
                + "completed_phases INTEGER NOT NULL DEFAULT 0, phase_total INTEGER NOT NULL DEFAULT 0,"
                + "executor_kind TEXT NOT NULL DEFAULT 'native-termux', executor_name TEXT NOT NULL DEFAULT '', executor_transport TEXT NOT NULL DEFAULT 'termux',"
                + "executor_workdir TEXT NOT NULL DEFAULT '', executor_linux_user TEXT NOT NULL DEFAULT 'termux', rollback_status TEXT NOT NULL DEFAULT '',"
                + "rollback_error TEXT NOT NULL DEFAULT '', rollback_total_paths INTEGER NOT NULL DEFAULT 0, rollback_verified_paths INTEGER NOT NULL DEFAULT 0,"
                + "rollback_backup_healthy INTEGER NOT NULL DEFAULT 0, uncommitted_changes INTEGER NOT NULL DEFAULT 0,"
                + "report_path TEXT NOT NULL DEFAULT '', report_generated_at TEXT NOT NULL DEFAULT '', final_verdict TEXT NOT NULL DEFAULT '',"
                + "commit_status TEXT NOT NULL DEFAULT 'not-requested', commit_hash TEXT NOT NULL DEFAULT '', initial_git_head TEXT NOT NULL DEFAULT '',"
                + "final_git_head TEXT NOT NULL DEFAULT '', report_warnings INTEGER NOT NULL DEFAULT 0, report_errors INTEGER NOT NULL DEFAULT 0)");
        db.execSQL("CREATE INDEX IF NOT EXISTS validation_sessions_active_idx ON validation_sessions(state, updated_at_ms DESC)");
        db.execSQL("CREATE TABLE IF NOT EXISTS validation_phases ("
                + "session_id TEXT NOT NULL, phase_key TEXT NOT NULL, name TEXT NOT NULL, label TEXT NOT NULL, phase_index INTEGER NOT NULL, phase_total INTEGER NOT NULL,"
                + "status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_verdict TEXT NOT NULL DEFAULT '', current_attempt_id TEXT NOT NULL DEFAULT '',"
                + "failure_summary TEXT NOT NULL DEFAULT '', log_path TEXT NOT NULL DEFAULT '', invalidated_reason TEXT NOT NULL DEFAULT '', dependencies_json TEXT NOT NULL DEFAULT '[]',"
                + "input_domains_json TEXT NOT NULL DEFAULT '[]', duration_ms INTEGER NOT NULL DEFAULT 0, progress INTEGER NOT NULL DEFAULT 0,"
                + "progress_detail TEXT NOT NULL DEFAULT '', progress_completed INTEGER NOT NULL DEFAULT -1, progress_total_value INTEGER NOT NULL DEFAULT -1,"
                + "progress_unit TEXT NOT NULL DEFAULT '', progress_updated_at TEXT NOT NULL DEFAULT '', workspace TEXT NOT NULL DEFAULT '', command_json TEXT NOT NULL DEFAULT '[]',"
                + "signal INTEGER NOT NULL DEFAULT 0, issues_json TEXT NOT NULL DEFAULT '[]', classification_evidence_json TEXT NOT NULL DEFAULT '[]',"
                + "supervisor_events_json TEXT NOT NULL DEFAULT '[]', input_snapshot_before_json TEXT NOT NULL DEFAULT '{}', input_snapshot_after_json TEXT NOT NULL DEFAULT '{}',"
                + "required INTEGER NOT NULL DEFAULT 1, supports_progress INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(session_id, phase_key),"
                + "FOREIGN KEY(session_id) REFERENCES validation_sessions(session_id) ON DELETE CASCADE)");
        db.execSQL("CREATE INDEX IF NOT EXISTS validation_phases_order_idx ON validation_phases(session_id, phase_index)");
        db.execSQL("CREATE TABLE IF NOT EXISTS validation_attempts ("
                + "session_id TEXT NOT NULL, phase_key TEXT NOT NULL, attempt_id TEXT NOT NULL, verdict TEXT NOT NULL DEFAULT '', failure_kind TEXT NOT NULL DEFAULT '',"
                + "category TEXT NOT NULL DEFAULT '', return_code INTEGER NOT NULL DEFAULT -1, duration_ms INTEGER NOT NULL DEFAULT 0, output_tail TEXT NOT NULL DEFAULT '',"
                + "log_path TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL DEFAULT '', finished_at TEXT NOT NULL DEFAULT '', recommended_profile TEXT NOT NULL DEFAULT '',"
                + "session_revision INTEGER NOT NULL DEFAULT 0, signal INTEGER NOT NULL DEFAULT 0, argv_json TEXT NOT NULL DEFAULT '[]', issues_json TEXT NOT NULL DEFAULT '[]',"
                + "input_snapshot_before_json TEXT NOT NULL DEFAULT '{}', input_snapshot_after_json TEXT NOT NULL DEFAULT '{}', classification_evidence_json TEXT NOT NULL DEFAULT '[]',"
                + "supervisor_events_json TEXT NOT NULL DEFAULT '[]', resource_profile TEXT NOT NULL DEFAULT '', retryable INTEGER NOT NULL DEFAULT 0,"
                + "automatic_retry INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(session_id, phase_key, attempt_id),"
                + "FOREIGN KEY(session_id, phase_key) REFERENCES validation_phases(session_id, phase_key) ON DELETE CASCADE)");
    }

    synchronized void upsertRun(RunRecord run) {
        SQLiteDatabase db = getWritableDatabase();
        if (!run.pinned) {
            try (Cursor cursor = db.query("runs", new String[]{"pinned"}, "run_id=?",
                    new String[]{run.runId}, null, null, null, "1")) {
                if (cursor.moveToFirst() && cursor.getInt(0) != 0) run.pinned = true;
            }
        }
        db.insertWithOnConflict("runs", null, runValues(run), SQLiteDatabase.CONFLICT_REPLACE);
    }

    synchronized void setRunPinned(String runId, boolean pinned) {
        ContentValues values = new ContentValues();
        values.put("pinned", pinned ? 1 : 0);
        getWritableDatabase().update("runs", values, "run_id=?", new String[]{runId});
    }

    synchronized List<RunRecord> allRuns() {
        List<RunRecord> values = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().query("runs", null, null, null, null, null,
                "updated_at_ms DESC")) {
            while (cursor.moveToNext()) values.add(readRun(cursor));
        }
        return values;
    }

    synchronized RunRecord findRun(String runId) {
        try (Cursor cursor = getReadableDatabase().query("runs", null, "run_id=?",
                new String[]{runId}, null, null, null, "1")) {
            return cursor.moveToFirst() ? readRun(cursor) : null;
        }
    }

    synchronized List<RunRecord> runsForRepository(RepositoryRecord repository) {
        List<RunRecord> values = new ArrayList<>();
        String selection = "repository_id=? OR repo=? OR workdir=?";
        String path = repository.canonicalPath;
        try (Cursor cursor = getReadableDatabase().query("runs", null, selection,
                new String[]{repository.repositoryId, path, path}, null, null, "updated_at_ms DESC")) {
            while (cursor.moveToNext()) values.add(readRun(cursor));
        }
        return values;
    }

    synchronized List<RunRecord> runsForCommit(String repositoryId, String commitHash) {
        List<RunRecord> values = new ArrayList<>();
        String wanted = commitHash == null ? "" : commitHash.trim().toLowerCase(java.util.Locale.ROOT);
        if (wanted.length() < 7) return values;
        String id = repositoryId == null ? "" : repositoryId;
        RepositoryRecord repository = repositoryInDatabase(getReadableDatabase(), id);
        String path = repository == null ? "" : repository.canonicalPath;
        try (Cursor cursor = getReadableDatabase().query("runs", null,
                "commit_hash<>'' AND (repository_id=? OR repo=? OR workdir=?)",
                new String[]{id, path, path}, null, null, "updated_at_ms DESC")) {
            while (cursor.moveToNext()) {
                RunRecord run = readRun(cursor);
                String actual = run.commitHash == null ? "" : run.commitHash.trim().toLowerCase(java.util.Locale.ROOT);
                if (actual.length() >= 7 && (actual.equals(wanted) || actual.startsWith(wanted) || wanted.startsWith(actual))) {
                    values.add(run);
                }
            }
        }
        return values;
    }

    synchronized boolean deleteRun(String runId) {
        SQLiteDatabase db = getWritableDatabase();
        db.beginTransaction();
        try {
            db.delete("log_entries_fts", "run_id=?", new String[]{runId});
            db.delete("log_entries", "run_id=?", new String[]{runId});
            db.delete("repository_events", "run_id=?", new String[]{runId});
            boolean removed = db.delete("runs", "run_id=?", new String[]{runId}) > 0;
            db.setTransactionSuccessful();
            return removed;
        } finally {
            db.endTransaction();
        }
    }

    synchronized void clearRuns() {
        SQLiteDatabase db = getWritableDatabase();
        db.beginTransaction();
        try {
            db.delete("log_entries_fts", null, null);
            db.delete("log_entries", null, null);
            db.delete("repository_events", "run_id<>''", null);
            db.delete("runs", null, null);
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
    }

    synchronized void addLogEntry(String runId, long timestamp, String severity, String category,
                                  String source, String phase, String stream, String message, String raw) {
        ContentValues values = new ContentValues();
        values.put("run_id", runId);
        values.put("timestamp_ms", timestamp);
        values.put("severity", severity);
        values.put("category", category);
        values.put("source", source);
        values.put("phase", phase);
        values.put("tool", "");
        values.put("stream", stream);
        values.put("message", message);
        values.put("raw_text", raw);
        values.put("file_path", "");
        values.put("line_number", -1);
        values.put("diagnostic_code", "");
        values.put("metadata_json", "");
        SQLiteDatabase db = getWritableDatabase();
        long id = db.insert("log_entries", null, values);
        if (id >= 0) {
            ContentValues fts = new ContentValues();
            fts.put("docid", id);
            fts.put("run_id", runId);
            fts.put("message", message);
            fts.put("raw_text", raw);
            fts.put("file_path", "");
            fts.put("diagnostic_code", "");
            fts.put("tool", "");
            db.insert("log_entries_fts", null, fts);
        }
    }

    synchronized List<LogLine> logLines(String runId, boolean problemsOnly) {
        List<LogLine> result = new ArrayList<>();
        String selection = problemsOnly ? "run_id=? AND severity IN ('warning','error','fatal')" : "run_id=?";
        try (Cursor cursor = getReadableDatabase().query("log_entries",
                new String[]{"timestamp_ms", "severity", "category", "source", "phase", "stream", "message", "raw_text", "file_path", "line_number", "tool", "diagnostic_code"},
                selection, new String[]{runId}, null, null, "timestamp_ms, entry_id")) {
            while (cursor.moveToNext()) {
                LogLine line = new LogLine();
                line.timestampMs = cursor.getLong(0);
                line.severity = cursor.getString(1);
                line.category = cursor.getString(2);
                line.source = cursor.getString(3);
                line.phase = cursor.getString(4);
                line.stream = cursor.getString(5);
                line.message = cursor.getString(6);
                line.raw = cursor.getString(7);
                line.filePath = cursor.getString(8);
                line.lineNumber = cursor.getInt(9);
                line.tool = cursor.getString(10);
                line.diagnosticCode = cursor.getString(11);
                result.add(line);
            }
        }
        return result;
    }

    synchronized Set<String> matchingRunIds(String query) {
        Set<String> result = new HashSet<>();
        String clean = query == null ? "" : query.trim();
        if (clean.isEmpty()) return result;
        String expression = "\"" + clean.replace("\"", "\"\"") + "\"";
        try (Cursor cursor = getReadableDatabase().rawQuery(
                "SELECT DISTINCT run_id FROM log_entries_fts WHERE log_entries_fts MATCH ?",
                new String[]{expression})) {
            while (cursor.moveToNext()) result.add(cursor.getString(0));
        } catch (RuntimeException ignored) {
            String pattern = "%" + clean.toLowerCase(java.util.Locale.ROOT) + "%";
            try (Cursor cursor = getReadableDatabase().rawQuery(
                    "SELECT DISTINCT run_id FROM log_entries WHERE lower(message) LIKE ? OR lower(raw_text) LIKE ? OR lower(file_path) LIKE ? OR lower(diagnostic_code) LIKE ? OR lower(tool) LIKE ?",
                    new String[]{pattern, pattern, pattern, pattern, pattern})) {
                while (cursor.moveToNext()) result.add(cursor.getString(0));
            }
        }
        return result;
    }

    synchronized Set<String> runIdsWithSeverity(String severity) {
        Set<String> result = new HashSet<>();
        String clean = severity == null ? "" : severity.trim().toLowerCase(java.util.Locale.ROOT);
        if (clean.isEmpty() || clean.equals("all")) return result;
        try (Cursor cursor = getReadableDatabase().query(true, "log_entries",
                new String[]{"run_id"}, "severity=?", new String[]{clean},
                null, null, null, null)) {
            while (cursor.moveToNext()) result.add(cursor.getString(0));
        }
        return result;
    }

    synchronized void upsertLogSource(String sourceId, String displayName, String sourceType,
                                      String rootPath, String environment, long scannedAt, String error) {
        ContentValues values = new ContentValues();
        values.put("source_id", sourceId);
        values.put("display_name", displayName);
        values.put("source_type", sourceType);
        values.put("root_path", rootPath);
        values.put("environment", environment);
        values.put("enabled", 1);
        values.put("last_scan_at", scannedAt);
        values.put("last_error", error == null ? "" : error);
        getWritableDatabase().insertWithOnConflict("log_sources", null, values, SQLiteDatabase.CONFLICT_REPLACE);
    }

    synchronized FileCursor fileCursor(String sourceId, String path) {
        try (Cursor cursor = getReadableDatabase().query("file_cursors", null,
                "source_id=? AND path=?", new String[]{sourceId, path}, null, null, null, "1")) {
            if (!cursor.moveToFirst()) return null;
            FileCursor value = new FileCursor();
            value.sourceId = string(cursor, "source_id");
            value.path = string(cursor, "path");
            value.inode = string(cursor, "inode");
            value.byteOffset = number(cursor, "byte_offset");
            value.sizeBytes = number(cursor, "size_bytes");
            value.modifiedAt = number(cursor, "modified_at");
            value.parserType = string(cursor, "parser_type");
            value.partialLine = string(cursor, "partial_line");
            value.lastScanAt = number(cursor, "last_scan_at");
            return value;
        }
    }

    synchronized void upsertFileCursor(FileCursor cursor) {
        ContentValues values = new ContentValues();
        values.put("source_id", cursor.sourceId);
        values.put("path", cursor.path);
        values.put("inode", cursor.inode);
        values.put("byte_offset", cursor.byteOffset);
        values.put("size_bytes", cursor.sizeBytes);
        values.put("modified_at", cursor.modifiedAt);
        values.put("parser_type", cursor.parserType);
        values.put("partial_line", cursor.partialLine);
        values.put("last_scan_at", cursor.lastScanAt);
        getWritableDatabase().insertWithOnConflict("file_cursors", null, values, SQLiteDatabase.CONFLICT_REPLACE);
    }

    synchronized boolean hasIndexedLogEntry(String runId, String source, String filePath, long sequence) {
        if (sequence <= 0L) return false;
        try (Cursor cursor = getReadableDatabase().query("log_entries", new String[]{"entry_id"},
                "run_id=? AND source=? AND file_path=? AND sequence_value=?",
                new String[]{runId, source, filePath, String.valueOf(sequence)}, null, null, null, "1")) {
            return cursor.moveToFirst();
        }
    }

    synchronized void addIndexedLogEntry(String runId, long sequence, long timestamp, String severity,
                                         String category, String source, String phase, String tool,
                                         String stream, String message, String raw, String filePath,
                                         int lineNumber, String diagnosticCode, String metadataJson) {
        ContentValues values = new ContentValues();
        values.put("run_id", runId);
        values.put("sequence_value", sequence);
        values.put("timestamp_ms", timestamp);
        values.put("severity", severity);
        values.put("category", category);
        values.put("source", source);
        values.put("phase", phase);
        values.put("tool", tool);
        values.put("stream", stream);
        values.put("message", message);
        values.put("raw_text", raw);
        values.put("file_path", filePath);
        values.put("line_number", lineNumber);
        values.put("diagnostic_code", diagnosticCode);
        values.put("metadata_json", metadataJson);
        SQLiteDatabase db = getWritableDatabase();
        long id = db.insert("log_entries", null, values);
        if (id < 0) return;
        ContentValues fts = new ContentValues();
        fts.put("docid", id);
        fts.put("run_id", runId);
        fts.put("message", message);
        fts.put("raw_text", raw);
        fts.put("file_path", filePath);
        fts.put("diagnostic_code", diagnosticCode);
        fts.put("tool", tool);
        db.insert("log_entries_fts", null, fts);
    }

    synchronized void upsertRepository(RepositoryRecord repo) {
        SQLiteDatabase db = getWritableDatabase();
        ContentValues values = repositoryValues(repo);
        int updated = db.update("repositories", values, "repository_id=?",
                new String[]{repo.repositoryId});
        if (updated == 0) {
            db.insertWithOnConflict("repositories", null, values, SQLiteDatabase.CONFLICT_IGNORE);
        }
    }

    synchronized void rekeyRepository(String oldId, RepositoryRecord replacement) {
        if (replacement == null || replacement.repositoryId.isEmpty()) return;
        if (replacement.repositoryId.equals(oldId)) {
            upsertRepository(replacement);
            return;
        }
        SQLiteDatabase db = getWritableDatabase();
        db.beginTransaction();
        try {
            RepositoryRecord existing = repositoryInDatabase(db, replacement.repositoryId);
            if (existing != null) mergeRepository(existing, replacement);
            ContentValues values = repositoryValues(replacement);
            int updated = db.update("repositories", values, "repository_id=?",
                    new String[]{replacement.repositoryId});
            if (updated == 0) db.insertOrThrow("repositories", null, values);

            ContentValues runLink = new ContentValues();
            runLink.put("repository_id", replacement.repositoryId);
            db.update("runs", runLink, "repository_id=?", new String[]{oldId});

            ContentValues eventLink = new ContentValues();
            eventLink.put("repository_id", replacement.repositoryId);
            db.update("repository_events", eventLink, "repository_id=?", new String[]{oldId});

            ContentValues operationLink = new ContentValues();
            operationLink.put("repository_id", replacement.repositoryId);
            db.update("repository_operations", operationLink, "repository_id=?", new String[]{oldId});
            db.delete("repositories", "repository_id=?", new String[]{oldId});
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
    }

    synchronized RepositoryRecord repository(String id) {
        try (Cursor cursor = getReadableDatabase().query("repositories", null, "repository_id=?",
                new String[]{id}, null, null, null, "1")) {
            return cursor.moveToFirst() ? readRepository(cursor) : null;
        }
    }

    synchronized List<RepositoryRecord> repositories() {
        List<RepositoryRecord> values = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().query("repositories", null, null, null,
                null, null, "saved DESC, active_run_count DESC, last_used_at DESC, display_name COLLATE NOCASE")) {
            while (cursor.moveToNext()) values.add(readRepository(cursor));
        }
        return values;
    }

    synchronized void deleteRepository(String id) {
        getWritableDatabase().delete("repositories", "repository_id=?", new String[]{id});
    }

    synchronized void removeTransientRepositories() {
        getWritableDatabase().delete("repositories", "saved=0 AND active_run_count=0", null);
    }

    synchronized RetentionReport applyRetention(RetentionPolicy policy, long now, boolean compactDatabase) {
        RetentionReport report = new RetentionReport();
        SQLiteDatabase db = getWritableDatabase();
        report.databaseBytesBefore = databaseBytes(db);
        long runCutoff = now - policy.completedRunDays * 86_400_000L;
        long operationCutoff = now - policy.repositoryOperationDays * 86_400_000L;
        long eventCutoff = now - policy.repositoryEventDays * 86_400_000L;
        Set<String> runIds = new HashSet<>();
        String terminal = "status IN ('ok','failed','cancelled','interrupted','skipped') AND pinned=0";
        try (Cursor cursor = db.query("runs", new String[]{"run_id"}, terminal + " AND updated_at_ms<?",
                new String[]{String.valueOf(runCutoff)}, null, null, null)) {
            while (cursor.moveToNext()) runIds.add(cursor.getString(0));
        }
        try (Cursor cursor = db.rawQuery("SELECT run_id FROM runs WHERE " + terminal
                + " ORDER BY updated_at_ms DESC LIMIT -1 OFFSET ?",
                new String[]{String.valueOf(policy.maximumCompletedRuns)})) {
            while (cursor.moveToNext()) runIds.add(cursor.getString(0));
        }
        db.beginTransaction();
        try {
            for (String runId : runIds) {
                report.logEntriesRemoved += count(db, "log_entries", "run_id=?", new String[]{runId});
                db.delete("log_entries_fts", "run_id=?", new String[]{runId});
                db.delete("log_entries", "run_id=?", new String[]{runId});
                db.delete("repository_events", "run_id=?", new String[]{runId});
                report.runsRemoved += db.delete("runs", "run_id=?", new String[]{runId});
            }
            report.operationsRemoved = db.delete("repository_operations",
                    "state NOT IN ('queued','running','orphaned') AND finished_at>0 AND finished_at<?",
                    new String[]{String.valueOf(operationCutoff)});
            report.eventsRemoved = db.delete("repository_events", "created_at<?",
                    new String[]{String.valueOf(eventCutoff)});
            db.delete("log_entries_fts", "run_id NOT IN (SELECT run_id FROM runs)", null);
            report.logEntriesRemoved += db.delete("log_entries",
                    "run_id NOT IN (SELECT run_id FROM runs)", null);
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
        if (compactDatabase) {
            try {
                db.execSQL("PRAGMA wal_checkpoint(TRUNCATE)");
                db.execSQL("VACUUM");
            } catch (RuntimeException failure) {
                report.error = "Cleanup completed, but database compaction failed: "
                        + (failure.getMessage() == null
                        ? failure.getClass().getSimpleName() : failure.getMessage());
            }
        }
        report.databaseBytesAfter = databaseBytes(db);
        return report;
    }

    synchronized DatabaseStats databaseStats() {
        SQLiteDatabase db = getReadableDatabase();
        DatabaseStats stats = new DatabaseStats();
        stats.runs = count(db, "runs", null, null);
        stats.pinnedRuns = count(db, "runs", "pinned=1", null);
        stats.logEntries = count(db, "log_entries", null, null);
        stats.repositories = count(db, "repositories", null, null);
        stats.operations = count(db, "repository_operations", null, null);
        stats.databaseBytes = databaseBytes(db);
        return stats;
    }

    private static int count(SQLiteDatabase db, String table, String selection, String[] arguments) {
        String query = "SELECT COUNT(*) FROM " + table + (selection == null ? "" : " WHERE " + selection);
        try (Cursor cursor = db.rawQuery(query, arguments)) {
            return cursor.moveToFirst() ? cursor.getInt(0) : 0;
        }
    }

    private static long databaseBytes(SQLiteDatabase db) {
        File file = new File(db.getPath());
        File wal = new File(db.getPath() + "-wal");
        File shm = new File(db.getPath() + "-shm");
        return (file.isFile() ? file.length() : 0L) + (wal.isFile() ? wal.length() : 0L)
                + (shm.isFile() ? shm.length() : 0L);
    }

    synchronized void resetActiveRunCounts() {
        ContentValues values = new ContentValues();
        values.put("active_run_count", 0);
        getWritableDatabase().update("repositories", values, null, null);
    }


    synchronized void upsertRepositoryOperation(RepositoryOperationRecord operation) {
        ContentValues values = new ContentValues();
        values.put("operation_id", operation.operationId);
        values.put("repository_id", operation.repositoryId);
        values.put("run_id", operation.runId);
        values.put("operation_type", operation.type);
        values.put("target", operation.target);
        values.put("state", operation.state);
        values.put("progress_label", operation.progressLabel);
        values.put("summary", operation.summary);
        values.put("queued_at", operation.queuedAt);
        values.put("queue_order", operation.queueOrder);
        values.put("started_at", operation.startedAt);
        values.put("finished_at", operation.finishedAt);
        values.put("exit_code", operation.exitCode);
        values.put("cancellable", operation.cancellable ? 1 : 0);
        getWritableDatabase().insertWithOnConflict("repository_operations", null, values,
                SQLiteDatabase.CONFLICT_REPLACE);
    }

    synchronized RepositoryOperationRecord repositoryOperation(String operationId) {
        try (Cursor cursor = getReadableDatabase().query("repository_operations", null,
                "operation_id=?", new String[]{operationId}, null, null, null, "1")) {
            return cursor.moveToFirst() ? readRepositoryOperation(cursor) : null;
        }
    }

    synchronized RepositoryOperationRecord repositoryOperationForRun(String runId) {
        try (Cursor cursor = getReadableDatabase().query("repository_operations", null,
                "run_id=?", new String[]{runId}, null, null, null, "1")) {
            return cursor.moveToFirst() ? readRepositoryOperation(cursor) : null;
        }
    }

    synchronized List<RepositoryOperationRecord> repositoryOperations(String repositoryId, int requestedLimit) {
        List<RepositoryOperationRecord> values = new ArrayList<>();
        int limit = Math.max(1, Math.min(250, requestedLimit));
        try (Cursor cursor = getReadableDatabase().query("repository_operations", null,
                "repository_id=?", new String[]{repositoryId}, null, null,
                "CASE state WHEN 'running' THEN 0 WHEN 'orphaned' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END, "
                        + "CASE WHEN state='queued' THEN queue_order ELSE 0 END ASC, queued_at DESC",
                String.valueOf(limit))) {
            while (cursor.moveToNext()) values.add(readRepositoryOperation(cursor));
        }
        return values;
    }

    synchronized List<RepositoryOperationRecord> activeRepositoryOperations() {
        List<RepositoryOperationRecord> values = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().query("repository_operations", null,
                "state IN ('queued','running','orphaned')", null, null, null,
                "CASE state WHEN 'running' THEN 0 WHEN 'orphaned' THEN 1 ELSE 2 END, queue_order, queued_at")) {
            while (cursor.moveToNext()) values.add(readRepositoryOperation(cursor));
        }
        return values;
    }

    synchronized void updateRepositoryOperationQueueOrder(String operationId, long queueOrder) {
        ContentValues values = new ContentValues();
        values.put("queue_order", queueOrder);
        getWritableDatabase().update("repository_operations", values,
                "operation_id=? AND state='queued'", new String[]{operationId});
    }

    synchronized void recoverInterruptedRepositoryOperations(long timestamp) {
        ContentValues values = new ContentValues();
        values.put("state", "interrupted");
        values.put("summary", "Android process stopped before the Git operation completed");
        values.put("finished_at", timestamp);
        values.put("exit_code", 130);
        values.put("cancellable", 0);
        getWritableDatabase().update("repository_operations", values,
                "state IN ('queued','running','orphaned')", null);
    }

    synchronized void addRepositoryEvent(String repositoryId, String runId, String type,
                                         String summary, long timestamp) {
        ContentValues values = new ContentValues();
        values.put("repository_id", repositoryId);
        values.put("run_id", runId == null ? "" : runId);
        values.put("event_type", type);
        values.put("summary", summary == null ? "" : summary);
        values.put("created_at", timestamp);
        getWritableDatabase().insert("repository_events", null, values);
    }

    synchronized List<RepositoryEvent> repositoryEvents(String repositoryId) {
        List<RepositoryEvent> values = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().query("repository_events",
                new String[]{"run_id", "event_type", "summary", "created_at"}, "repository_id=?",
                new String[]{repositoryId}, null, null, "created_at DESC", "200")) {
            while (cursor.moveToNext()) {
                RepositoryEvent event = new RepositoryEvent();
                event.runId = cursor.getString(0);
                event.type = cursor.getString(1);
                event.summary = cursor.getString(2);
                event.timestampMs = cursor.getLong(3);
                values.add(event);
            }
        }
        return values;
    }


    private static RepositoryOperationRecord readRepositoryOperation(Cursor cursor) {
        RepositoryOperationRecord operation = new RepositoryOperationRecord();
        operation.operationId = string(cursor, "operation_id");
        operation.repositoryId = string(cursor, "repository_id");
        operation.runId = string(cursor, "run_id");
        operation.type = string(cursor, "operation_type");
        operation.target = string(cursor, "target");
        operation.state = string(cursor, "state");
        operation.progressLabel = string(cursor, "progress_label");
        operation.summary = string(cursor, "summary");
        operation.queuedAt = number(cursor, "queued_at");
        operation.queueOrder = number(cursor, "queue_order");
        operation.startedAt = number(cursor, "started_at");
        operation.finishedAt = number(cursor, "finished_at");
        operation.exitCode = integer(cursor, "exit_code");
        operation.cancellable = integer(cursor, "cancellable") != 0;
        return operation;
    }

    private static ContentValues runValues(RunRecord run) {
        ContentValues value = new ContentValues();
        value.put("run_id", run.runId); value.put("title", run.title); value.put("status", run.status);
        value.put("phase", run.phase); value.put("detail", run.detail); value.put("repo", run.repo);
        value.put("repository_id", run.repositoryId); value.put("project_type", run.projectType);
        value.put("execution_environment", run.executionEnvironment); value.put("workdir", run.workdir);
        value.put("target", run.target); value.put("command", run.command); value.put("summary", run.summary);
        value.put("stdout", run.stdout); value.put("stderr", run.stderr); value.put("error", run.error);
        value.put("launch_log", run.launchLog); value.put("launch_mode", run.launchMode);
        value.put("last_progress_at_ms", run.lastProgressAtMs); value.put("progress", run.progress);
        value.put("current_value", run.current); value.put("total_value", run.total); value.put("unit_label", run.unitLabel);
        value.put("warnings", run.warnings); value.put("errors", run.errors); value.put("steps_json", run.stepsJson);
        value.put("indeterminate", run.indeterminate ? 1 : 0); value.put("pinned", run.pinned ? 1 : 0);
        value.put("started_at_ms", run.startedAtMs);
        value.put("updated_at_ms", run.updatedAtMs); value.put("duration_ms", run.durationMs);
        value.put("artifact", run.artifact); value.put("commit_hash", run.commitHash);
        value.put("transaction_backup", run.transactionBackup); value.put("log_path", run.logPath);
        value.put("log_source_id", run.logSourceId); value.put("exit_code", run.exitCode);
        return value;
    }

    private static RunRecord readRun(Cursor cursor) {
        RunRecord run = new RunRecord();
        run.runId = string(cursor, "run_id"); run.title = string(cursor, "title"); run.status = string(cursor, "status");
        run.phase = string(cursor, "phase"); run.detail = string(cursor, "detail"); run.repo = string(cursor, "repo");
        run.repositoryId = string(cursor, "repository_id"); run.projectType = string(cursor, "project_type");
        run.executionEnvironment = string(cursor, "execution_environment"); run.workdir = string(cursor, "workdir");
        run.target = string(cursor, "target"); run.command = string(cursor, "command"); run.summary = string(cursor, "summary");
        run.stdout = string(cursor, "stdout"); run.stderr = string(cursor, "stderr"); run.error = string(cursor, "error");
        run.launchLog = string(cursor, "launch_log"); run.launchMode = string(cursor, "launch_mode");
        run.lastProgressAtMs = number(cursor, "last_progress_at_ms"); run.progress = integer(cursor, "progress");
        run.current = integer(cursor, "current_value"); run.total = integer(cursor, "total_value");
        run.unitLabel = string(cursor, "unit_label"); run.warnings = integer(cursor, "warnings"); run.errors = integer(cursor, "errors");
        run.stepsJson = string(cursor, "steps_json"); run.indeterminate = integer(cursor, "indeterminate") != 0;
        run.pinned = integer(cursor, "pinned") != 0; run.startedAtMs = number(cursor, "started_at_ms"); run.updatedAtMs = number(cursor, "updated_at_ms");
        run.durationMs = number(cursor, "duration_ms"); run.artifact = string(cursor, "artifact");
        run.commitHash = string(cursor, "commit_hash"); run.transactionBackup = string(cursor, "transaction_backup");
        run.logPath = string(cursor, "log_path"); run.logSourceId = string(cursor, "log_source_id");
        run.exitCode = integer(cursor, "exit_code");
        return run;
    }


    private static RepositoryRecord repositoryInDatabase(SQLiteDatabase db, String id) {
        try (Cursor cursor = db.query("repositories", null, "repository_id=?",
                new String[]{id}, null, null, null, "1")) {
            return cursor.moveToFirst() ? readRepository(cursor) : null;
        }
    }

    private static void mergeRepository(RepositoryRecord target, RepositoryRecord source) {
        source.saved = source.saved || target.saved;
        source.activeRunCount = Math.max(source.activeRunCount, target.activeRunCount);
        source.lastUsedAt = Math.max(source.lastUsedAt, target.lastUsedAt);
        source.lastScannedAt = Math.max(source.lastScannedAt, target.lastScannedAt);
        if (source.displayName.isEmpty()) source.displayName = target.displayName;
        if (source.projectType.isEmpty() || source.projectType.equals("auto")) source.projectType = target.projectType;
        if (source.branch.isEmpty()) source.branch = target.branch;
        if (source.upstream.isEmpty()) source.upstream = target.upstream;
        if (source.remote.isEmpty()) source.remote = target.remote;
        if (source.configuredPath.isEmpty()) source.configuredPath = target.configuredPath;
        if (source.hostPath.isEmpty()) source.hostPath = target.hostPath;
    }

    private static ContentValues repositoryValues(RepositoryRecord repo) {
        ContentValues value = new ContentValues();
        value.put("repository_id", repo.repositoryId); value.put("display_name", repo.displayName);
        value.put("environment", repo.environment); value.put("chroot_name", repo.chrootName);
        value.put("linux_user", repo.linuxUser); value.put("configured_path", repo.configuredPath);
        value.put("canonical_path", repo.canonicalPath); value.put("host_path", repo.hostPath);
        value.put("git_root", repo.gitRoot); value.put("branch", repo.branch); value.put("upstream", repo.upstream);
        value.put("remote", repo.remote); value.put("status", repo.status); value.put("dirty", repo.dirty ? 1 : 0);
        value.put("ahead_count", repo.aheadCount); value.put("behind_count", repo.behindCount);
        value.put("active_run_count", repo.activeRunCount); value.put("saved", repo.saved ? 1 : 0);
        value.put("last_used_at", repo.lastUsedAt); value.put("last_scanned_at", repo.lastScannedAt);
        value.put("last_error", repo.lastError); value.put("project_type", repo.projectType);
        return value;
    }

    private static RepositoryRecord readRepository(Cursor cursor) {
        RepositoryRecord repo = new RepositoryRecord();
        repo.repositoryId = string(cursor, "repository_id"); repo.displayName = string(cursor, "display_name");
        repo.environment = string(cursor, "environment"); repo.chrootName = string(cursor, "chroot_name");
        repo.linuxUser = string(cursor, "linux_user"); repo.configuredPath = string(cursor, "configured_path");
        repo.canonicalPath = string(cursor, "canonical_path"); repo.hostPath = string(cursor, "host_path");
        repo.gitRoot = string(cursor, "git_root"); repo.branch = string(cursor, "branch"); repo.upstream = string(cursor, "upstream");
        repo.remote = string(cursor, "remote"); repo.status = string(cursor, "status"); repo.dirty = integer(cursor, "dirty") != 0;
        repo.aheadCount = integer(cursor, "ahead_count"); repo.behindCount = integer(cursor, "behind_count");
        repo.activeRunCount = integer(cursor, "active_run_count"); repo.saved = integer(cursor, "saved") != 0;
        repo.lastUsedAt = number(cursor, "last_used_at"); repo.lastScannedAt = number(cursor, "last_scanned_at");
        repo.lastError = string(cursor, "last_error"); repo.projectType = string(cursor, "project_type");
        return repo;
    }

    private static String string(Cursor cursor, String column) { return cursor.getString(cursor.getColumnIndexOrThrow(column)); }
    private static int integer(Cursor cursor, String column) { return cursor.getInt(cursor.getColumnIndexOrThrow(column)); }
    private static long number(Cursor cursor, String column) { return cursor.getLong(cursor.getColumnIndexOrThrow(column)); }

    static final class DatabaseStats {
        int runs;
        int pinnedRuns;
        int logEntries;
        int repositories;
        int operations;
        long databaseBytes;
    }

    static final class LogLine {
        long timestampMs;
        String severity = "info";
        String category = "execution";
        String source = "android_app";
        String phase = "";
        String stream = "event";
        String message = "";
        String raw = "";
        String filePath = "";
        int lineNumber = -1;
        String tool = "";
        String diagnosticCode = "";
    }

    static final class FileCursor {
        String sourceId = "";
        String path = "";
        String inode = "";
        long byteOffset;
        long sizeBytes;
        long modifiedAt;
        String parserType = "text";
        String partialLine = "";
        long lastScanAt;
    }

    static final class RepositoryEvent {
        String runId = "";
        String type = "";
        String summary = "";
        long timestampMs;
    }
}
