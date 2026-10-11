package io.github.mikeceead.devtoolhud;

import android.content.Context;
import android.content.SharedPreferences;

final class AppSettings {
    private static final String PREFS = "app_settings";
    private static final String KEY_MONET = "monet";
    private static final String KEY_COMMAND = "command";
    private static final String KEY_WORKDIR = "workdir";
    private static final String KEY_BACKGROUND = "background";
    private static final String KEY_OVERLAY = "overlay_default";
    private static final String KEY_PAGE = "page";
    private static final String KEY_NAV_VERSION = "navigation_version";
    private static final String KEY_OPERATION = "operation";
    private static final String KEY_PROJECT_TYPE = "project_type";
    private static final String KEY_EXECUTION_ENVIRONMENT = "execution_environment";
    private static final String KEY_REPO = "repo";
    private static final String KEY_RUN_WORKDIR = "run_workdir";
    private static final String KEY_TARGET = "target";
    private static final String KEY_ARTIFACT = "artifact";
    private static final String KEY_PIPELINE_FILE = "pipeline_file";
    private static final String KEY_PIPELINE_ID = "pipeline_id";
    private static final String KEY_COMMIT_MESSAGE = "commit_message";
    private static final String KEY_CUSTOM_ARGUMENTS = "custom_arguments";
    private static final String KEY_ENVIRONMENT = "environment_assignments";
    private static final String KEY_EXTRA_GLOBAL = "extra_global_arguments";
    private static final String KEY_EXTRA_OPERATION = "extra_operation_arguments";
    private static final String KEY_CHROOT = "chroot";
    private static final String KEY_USE_CHROOT = "use_chroot";
    private static final String KEY_USE_ROOT_BRIDGE = "use_root_bridge";
    private static final String KEY_COPY = "copy";
    private static final String KEY_COPY_VIA_TERMUX = "copy_via_termux";
    private static final String KEY_AUTO_HUD = "auto_hud";
    private static final String KEY_VALIDATE = "validate";
    private static final String KEY_COMMIT = "commit";
    private static final String KEY_YES = "assume_yes";
    private static final String KEY_TEST_PROFILE = "test_profile";
    private static final String KEY_TEST_SELECTORS = "test_selectors";
    private static final String KEY_TEST_TAGS = "test_tags";
    private static final String KEY_TEST_EXCLUDE_TAGS = "test_exclude_tags";
    private static final String KEY_TEST_PATH = "test_path";
    private static final String KEY_TEST_CLASS = "test_class";
    private static final String KEY_TEST_METHOD = "test_method";
    private static final String KEY_LIST_TESTS = "list_tests";
    private static final String KEY_INSTRUMENTED = "instrumented";
    private static final String KEY_RUN_DRAFT = "run_draft_json";
    private static final String KEY_LOG_QUERY = "log_view_query";
    private static final String KEY_LOG_STATUS = "log_view_status";
    private static final String KEY_LOG_PROJECT = "log_view_project";
    private static final String KEY_LOG_SEVERITY = "log_view_severity";
    private static final String KEY_LOG_REPOSITORY = "log_view_repository";
    private static final String KEY_RETENTION_RUN_DAYS = "retention_run_days";
    private static final String KEY_RETENTION_MAX_RUNS = "retention_max_runs";
    private static final String KEY_RETENTION_OPERATION_DAYS = "retention_operation_days";
    private static final String KEY_RETENTION_EVENT_DAYS = "retention_event_days";
    private static final String KEY_LAST_RETENTION_AT = "last_retention_at";
    private static final String KEY_SUPPORT_INCLUDE_COMMANDS = "support_include_commands";
    private static final String KEY_RUN_SECTION = "run_section";

    private AppSettings() {}

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static boolean useMonet(Context context) { return prefs(context).getBoolean(KEY_MONET, false); }
    static void setUseMonet(Context context, boolean value) { prefs(context).edit().putBoolean(KEY_MONET, value).apply(); }

    static String command(Context context) {
        return prefs(context).getString(KEY_COMMAND,
                "devtool --copy --auto-hud --yes --help");
    }
    static void setCommand(Context context, String value) { prefs(context).edit().putString(KEY_COMMAND, clean(value)).apply(); }

    static String workdir(Context context) { return prefs(context).getString(KEY_WORKDIR, "~/"); }
    static void setWorkdir(Context context, String value) {
        String clean = clean(value);
        prefs(context).edit().putString(KEY_WORKDIR, clean.isEmpty() ? "~/" : clean).apply();
    }

    static boolean runInBackground(Context context) { return prefs(context).getBoolean(KEY_BACKGROUND, true); }
    static void setRunInBackground(Context context, boolean value) { prefs(context).edit().putBoolean(KEY_BACKGROUND, value).apply(); }

    static boolean overlayByDefault(Context context) { return prefs(context).getBoolean(KEY_OVERLAY, true); }
    static void setOverlayByDefault(Context context, boolean value) { prefs(context).edit().putBoolean(KEY_OVERLAY, value).apply(); }

    static int page(Context context) {
        SharedPreferences preferences = prefs(context);
        int value = preferences.getInt(KEY_PAGE, 0);
        int version = preferences.getInt(KEY_NAV_VERSION, 1);
        if (version < 2) {
            if (value == 3) value = 4; // Settings moved after the new Repos destination.
            preferences.edit().putInt(KEY_PAGE, value).putInt(KEY_NAV_VERSION, 2).apply();
        }
        return value;
    }
    static void setPage(Context context, int value) {
        prefs(context).edit().putInt(KEY_PAGE, value).putInt(KEY_NAV_VERSION, 2).apply();
    }

    static String logQuery(Context context) { return prefs(context).getString(KEY_LOG_QUERY, ""); }
    static String logStatus(Context context) { return prefs(context).getString(KEY_LOG_STATUS, "all"); }
    static String logProject(Context context) { return prefs(context).getString(KEY_LOG_PROJECT, "all"); }
    static String logSeverity(Context context) { return prefs(context).getString(KEY_LOG_SEVERITY, "all"); }
    static String logRepository(Context context) { return prefs(context).getString(KEY_LOG_REPOSITORY, ""); }

    static void setLogView(Context context, String query, String status, String project,
                           String severity, String repositoryId) {
        prefs(context).edit()
                .putString(KEY_LOG_QUERY, clean(query))
                .putString(KEY_LOG_STATUS, clean(status).isEmpty() ? "all" : clean(status))
                .putString(KEY_LOG_PROJECT, clean(project).isEmpty() ? "all" : clean(project))
                .putString(KEY_LOG_SEVERITY, clean(severity).isEmpty() ? "all" : clean(severity))
                .putString(KEY_LOG_REPOSITORY, clean(repositoryId))
                .apply();
    }

    static void resetLogView(Context context) {
        prefs(context).edit()
                .remove(KEY_LOG_QUERY).remove(KEY_LOG_STATUS).remove(KEY_LOG_PROJECT)
                .remove(KEY_LOG_SEVERITY).remove(KEY_LOG_REPOSITORY).apply();
    }

    static int completedRunRetentionDays(Context context) {
        return prefs(context).getInt(KEY_RETENTION_RUN_DAYS, 30);
    }

    static int maximumCompletedRuns(Context context) {
        return prefs(context).getInt(KEY_RETENTION_MAX_RUNS, 500);
    }

    static int repositoryOperationRetentionDays(Context context) {
        return prefs(context).getInt(KEY_RETENTION_OPERATION_DAYS, 30);
    }

    static int repositoryEventRetentionDays(Context context) {
        return prefs(context).getInt(KEY_RETENTION_EVENT_DAYS, 90);
    }

    static void setRetentionPolicy(Context context, int runDays, int maximumRuns,
                                   int operationDays, int eventDays) {
        prefs(context).edit()
                .putInt(KEY_RETENTION_RUN_DAYS, runDays)
                .putInt(KEY_RETENTION_MAX_RUNS, maximumRuns)
                .putInt(KEY_RETENTION_OPERATION_DAYS, operationDays)
                .putInt(KEY_RETENTION_EVENT_DAYS, eventDays)
                .apply();
    }

    static String runSection(Context context) {
        return prefs(context).getString(KEY_RUN_SECTION, "command");
    }

    static void setRunSection(Context context, String value) {
        prefs(context).edit().putString(KEY_RUN_SECTION, "validation".equals(value) ? "validation" : "command").apply();
    }

    static boolean includeSupportCommands(Context context) {
        return prefs(context).getBoolean(KEY_SUPPORT_INCLUDE_COMMANDS, false);
    }

    static void setIncludeSupportCommands(Context context, boolean value) {
        prefs(context).edit().putBoolean(KEY_SUPPORT_INCLUDE_COMMANDS, value).apply();
    }

    static long lastRetentionAt(Context context) {
        return prefs(context).getLong(KEY_LAST_RETENTION_AT, 0L);
    }

    static void setLastRetentionAt(Context context, long value) {
        prefs(context).edit().putLong(KEY_LAST_RETENTION_AT, value).apply();
    }

    static DevtoolRunConfig runConfig(Context context) {
        SharedPreferences p = prefs(context);
        DevtoolRunConfig config = new DevtoolRunConfig();
        config.operation = p.getString(KEY_OPERATION, config.operation);
        config.projectType = p.getString(KEY_PROJECT_TYPE, config.projectType);
        config.executionEnvironment = p.getString(KEY_EXECUTION_ENVIRONMENT, config.executionEnvironment);
        config.repo = p.getString(KEY_REPO, config.repo);
        config.workdir = p.getString(KEY_RUN_WORKDIR, config.workdir);
        config.target = p.getString(KEY_TARGET, config.target);
        config.artifact = p.getString(KEY_ARTIFACT, config.artifact);
        config.pipelineFile = p.getString(KEY_PIPELINE_FILE, config.pipelineFile);
        config.pipelineId = p.getString(KEY_PIPELINE_ID, config.pipelineId);
        config.commitMessage = p.getString(KEY_COMMIT_MESSAGE, config.commitMessage);
        config.customArguments = p.getString(KEY_CUSTOM_ARGUMENTS, config.customArguments);
        config.environmentAssignments = p.getString(KEY_ENVIRONMENT, config.environmentAssignments);
        config.extraGlobalArguments = p.getString(KEY_EXTRA_GLOBAL, config.extraGlobalArguments);
        config.extraOperationArguments = p.getString(KEY_EXTRA_OPERATION, config.extraOperationArguments);
        config.chrootName = p.getString(KEY_CHROOT, config.chrootName);
        config.testProfile = p.getString(KEY_TEST_PROFILE, config.testProfile);
        config.testSelectors = p.getString(KEY_TEST_SELECTORS, config.testSelectors);
        config.testTags = p.getString(KEY_TEST_TAGS, config.testTags);
        config.testExcludeTags = p.getString(KEY_TEST_EXCLUDE_TAGS, config.testExcludeTags);
        config.testPath = p.getString(KEY_TEST_PATH, config.testPath);
        config.testClass = p.getString(KEY_TEST_CLASS, config.testClass);
        config.testMethod = p.getString(KEY_TEST_METHOD, config.testMethod);
        config.useChroot = p.getBoolean(KEY_USE_CHROOT, config.useChroot);
        config.useRootBridge = p.getBoolean(KEY_USE_ROOT_BRIDGE, config.useRootBridge);
        config.copy = p.getBoolean(KEY_COPY, config.copy);
        config.copyViaTermux = p.getBoolean(KEY_COPY_VIA_TERMUX, config.copyViaTermux);
        config.autoHud = p.getBoolean(KEY_AUTO_HUD, config.autoHud);
        config.validate = p.getBoolean(KEY_VALIDATE, config.validate);
        config.commit = p.getBoolean(KEY_COMMIT, config.commit);
        config.assumeYes = p.getBoolean(KEY_YES, config.assumeYes);
        config.overlay = p.getBoolean(KEY_OVERLAY, config.overlay);
        config.listTests = p.getBoolean(KEY_LIST_TESTS, config.listTests);
        config.instrumented = p.getBoolean(KEY_INSTRUMENTED, config.instrumented);
        return config;
    }


    static DevtoolRunConfig runDraft(Context context) {
        String raw = prefs(context).getString(KEY_RUN_DRAFT, "");
        if (raw == null || raw.trim().isEmpty()) return null;
        try {
            org.json.JSONObject value = new org.json.JSONObject(raw);
            DevtoolRunConfig config = new DevtoolRunConfig();
            config.operation = value.optString("operation", config.operation);
            config.projectType = value.optString("project_type", config.projectType);
            config.executionEnvironment = value.optString("execution_environment", config.executionEnvironment);
            config.repo = value.optString("repo", config.repo);
            config.workdir = value.optString("workdir", config.workdir);
            config.target = value.optString("target", config.target);
            config.artifact = value.optString("artifact", config.artifact);
            config.pipelineFile = value.optString("pipeline_file", config.pipelineFile);
            config.pipelineId = value.optString("pipeline_id", config.pipelineId);
            config.commitMessage = value.optString("commit_message", config.commitMessage);
            config.customArguments = value.optString("custom_arguments", config.customArguments);
            config.environmentAssignments = value.optString("environment_assignments", config.environmentAssignments);
            config.extraGlobalArguments = value.optString("extra_global_arguments", config.extraGlobalArguments);
            config.extraOperationArguments = value.optString("extra_operation_arguments", config.extraOperationArguments);
            config.chrootName = value.optString("chroot_name", config.chrootName);
            config.testProfile = value.optString("test_profile", config.testProfile);
            config.testSelectors = value.optString("test_selectors", config.testSelectors);
            config.testTags = value.optString("test_tags", config.testTags);
            config.testExcludeTags = value.optString("test_exclude_tags", config.testExcludeTags);
            config.testPath = value.optString("test_path", config.testPath);
            config.testClass = value.optString("test_class", config.testClass);
            config.testMethod = value.optString("test_method", config.testMethod);
            config.useChroot = value.optBoolean("use_chroot", config.useChroot);
            config.useRootBridge = value.optBoolean("use_root_bridge", config.useRootBridge);
            config.copy = value.optBoolean("copy", config.copy);
            config.copyViaTermux = value.optBoolean("copy_via_termux", config.copyViaTermux);
            config.autoHud = value.optBoolean("auto_hud", config.autoHud);
            config.validate = value.optBoolean("validate", config.validate);
            config.commit = value.optBoolean("commit", config.commit);
            config.assumeYes = value.optBoolean("assume_yes", config.assumeYes);
            config.overlay = value.optBoolean("overlay", config.overlay);
            config.listTests = value.optBoolean("list_tests", config.listTests);
            config.instrumented = value.optBoolean("instrumented", config.instrumented);
            return config;
        } catch (org.json.JSONException error) {
            return null;
        }
    }

    static void setRunDraft(Context context, DevtoolRunConfig config) {
        if (config == null) {
            prefs(context).edit().remove(KEY_RUN_DRAFT).apply();
            return;
        }
        org.json.JSONObject value = new org.json.JSONObject();
        try {
            value.put("operation", config.operation);
            value.put("project_type", config.projectType);
            value.put("execution_environment", config.executionEnvironment);
            value.put("repo", config.repo);
            value.put("workdir", config.workdir);
            value.put("target", config.target);
            value.put("artifact", config.artifact);
            value.put("pipeline_file", config.pipelineFile);
            value.put("pipeline_id", config.pipelineId);
            value.put("commit_message", config.commitMessage);
            value.put("custom_arguments", config.customArguments);
            value.put("environment_assignments", config.environmentAssignments);
            value.put("extra_global_arguments", config.extraGlobalArguments);
            value.put("extra_operation_arguments", config.extraOperationArguments);
            value.put("chroot_name", config.chrootName);
            value.put("test_profile", config.testProfile);
            value.put("test_selectors", config.testSelectors);
            value.put("test_tags", config.testTags);
            value.put("test_exclude_tags", config.testExcludeTags);
            value.put("test_path", config.testPath);
            value.put("test_class", config.testClass);
            value.put("test_method", config.testMethod);
            value.put("use_chroot", config.useChroot);
            value.put("use_root_bridge", config.useRootBridge);
            value.put("copy", config.copy);
            value.put("copy_via_termux", config.copyViaTermux);
            value.put("auto_hud", config.autoHud);
            value.put("validate", config.validate);
            value.put("commit", config.commit);
            value.put("assume_yes", config.assumeYes);
            value.put("overlay", config.overlay);
            value.put("list_tests", config.listTests);
            value.put("instrumented", config.instrumented);
        } catch (org.json.JSONException ignored) {
        }
        prefs(context).edit().putString(KEY_RUN_DRAFT, value.toString()).apply();
    }

    static void clearRunDraft(Context context) {
        prefs(context).edit().remove(KEY_RUN_DRAFT).apply();
    }

    static void setRunConfig(Context context, DevtoolRunConfig config) {
        prefs(context).edit()
                .putString(KEY_OPERATION, clean(config.operation))
                .putString(KEY_PROJECT_TYPE, clean(config.projectType))
                .putString(KEY_EXECUTION_ENVIRONMENT, clean(config.executionEnvironment))
                .putString(KEY_REPO, clean(config.repo))
                .putString(KEY_RUN_WORKDIR, clean(config.workdir))
                .putString(KEY_TARGET, clean(config.target))
                .putString(KEY_ARTIFACT, clean(config.artifact))
                .putString(KEY_PIPELINE_FILE, clean(config.pipelineFile))
                .putString(KEY_PIPELINE_ID, clean(config.pipelineId))
                .putString(KEY_COMMIT_MESSAGE, clean(config.commitMessage))
                .putString(KEY_CUSTOM_ARGUMENTS, clean(config.customArguments))
                .putString(KEY_ENVIRONMENT, clean(config.environmentAssignments))
                .putString(KEY_EXTRA_GLOBAL, clean(config.extraGlobalArguments))
                .putString(KEY_EXTRA_OPERATION, clean(config.extraOperationArguments))
                .putString(KEY_CHROOT, clean(config.chrootName))
                .putString(KEY_TEST_PROFILE, clean(config.testProfile))
                .putString(KEY_TEST_SELECTORS, clean(config.testSelectors))
                .putString(KEY_TEST_TAGS, clean(config.testTags))
                .putString(KEY_TEST_EXCLUDE_TAGS, clean(config.testExcludeTags))
                .putString(KEY_TEST_PATH, clean(config.testPath))
                .putString(KEY_TEST_CLASS, clean(config.testClass))
                .putString(KEY_TEST_METHOD, clean(config.testMethod))
                .putBoolean(KEY_USE_CHROOT, config.useChroot)
                .putBoolean(KEY_USE_ROOT_BRIDGE, config.useRootBridge)
                .putBoolean(KEY_COPY, config.copy)
                .putBoolean(KEY_COPY_VIA_TERMUX, config.copyViaTermux)
                .putBoolean(KEY_AUTO_HUD, config.autoHud)
                .putBoolean(KEY_VALIDATE, config.validate)
                .putBoolean(KEY_COMMIT, config.commit)
                .putBoolean(KEY_YES, config.assumeYes)
                .putBoolean(KEY_OVERLAY, config.overlay)
                .putBoolean(KEY_LIST_TESTS, config.listTests)
                .putBoolean(KEY_INSTRUMENTED, config.instrumented)
                .apply();
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
