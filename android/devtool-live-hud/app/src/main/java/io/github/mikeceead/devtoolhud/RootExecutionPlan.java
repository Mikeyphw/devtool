package io.github.mikeceead.devtoolhud;

import android.annotation.SuppressLint;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

@SuppressLint("SdCardPath")
final class RootExecutionPlan {
    static final String METHOD_ROOT = "root";
    static final String METHOD_DIRECT_ROOT = "direct-root";
    static final String METHOD_RUNUSER = "runuser";
    static final String METHOD_SU = "su";
    static final String METHOD_TERMUX_UID = "termux-uid";

    String environment = "root";
    String chrootName = "";
    String linuxUser = "root";
    String linuxHome = "/root";
    String selectedShell = "/bin/sh";
    String shellName = "sh";
    String userSwitchMethod = METHOD_ROOT;
    boolean usable = true;
    boolean userExists = true;
    boolean runuserAvailable;
    boolean runuserWorks;
    boolean suAvailable;
    boolean suWorks;
    boolean directRootFallback;
    boolean zshAvailable;
    boolean bashAvailable;
    boolean shAvailable = true;
    String rejectedShellCandidates = "";
    String rejectedUserSwitchCandidates = "";
    String issueKey = "root_execution_plan_ok";
    String summary = "Root execution plan selected.";

    static RootExecutionPlan root() {
        RootExecutionPlan plan = new RootExecutionPlan();
        plan.environment = "root";
        plan.linuxUser = "root";
        plan.userSwitchMethod = METHOD_ROOT;
        plan.summary = "Root commands run directly through su -c.";
        return plan;
    }

    static RootExecutionPlan termux() {
        RootExecutionPlan plan = new RootExecutionPlan();
        plan.environment = "native-termux";
        plan.linuxUser = "termux";
        plan.selectedShell = "/data/data/com.termux/files/usr/bin/sh";
        plan.shellName = "sh";
        plan.userSwitchMethod = METHOD_TERMUX_UID;
        plan.summary = "Termux commands run as the installed Termux package UID.";
        return plan;
    }

    static RootExecutionPlan chrootFallback(RepositoryLocation location, String issue, String text) {
        RootExecutionPlan plan = new RootExecutionPlan();
        plan.environment = "chroot";
        plan.chrootName = clean(location.chrootName);
        plan.linuxUser = clean(location.linuxUser);
        plan.selectedShell = "/bin/sh";
        plan.shellName = "sh";
        plan.userSwitchMethod = METHOD_DIRECT_ROOT;
        plan.directRootFallback = true;
        plan.usable = false;
        plan.issueKey = clean(issue).isEmpty() ? "root_execution_plan_probe_failed" : issue;
        plan.summary = clean(text).isEmpty() ? "The chroot execution planner could not complete." : clean(text);
        return plan;
    }

    static RootExecutionPlan fromProbe(RepositoryLocation location, String output, int exitCode, boolean timedOut) {
        RootExecutionPlan plan = new RootExecutionPlan();
        plan.environment = "chroot";
        plan.chrootName = clean(location.chrootName);
        plan.linuxUser = clean(location.linuxUser);
        Map<String, String> values = parse(output);
        plan.zshAvailable = bool(values.get("zsh_available"));
        plan.bashAvailable = bool(values.get("bash_available"));
        plan.shAvailable = bool(values.get("sh_available"));
        plan.userExists = bool(values.get("user_exists"));
        plan.runuserAvailable = bool(values.get("runuser_available"));
        plan.runuserWorks = bool(values.get("runuser_works"));
        plan.suAvailable = bool(values.get("su_available"));
        plan.suWorks = bool(values.get("su_works"));
        plan.selectedShell = first(values.get("selected_shell"), "/bin/sh");
        plan.linuxHome = first(values.get("linux_home"), plan.linuxUser.equals("root") ? "/root" : "");
        plan.shellName = first(values.get("selected_shell_name"), nameOf(plan.selectedShell));
        plan.userSwitchMethod = first(values.get("user_switch_method"), METHOD_DIRECT_ROOT);
        plan.rejectedShellCandidates = clean(values.get("rejected_shell_candidates"));
        plan.rejectedUserSwitchCandidates = clean(values.get("rejected_user_switch_candidates"));
        plan.directRootFallback = plan.userSwitchMethod.equals(METHOD_DIRECT_ROOT)
                && !plan.linuxUser.isEmpty() && !plan.linuxUser.equals("root");
        plan.usable = !timedOut && exitCode == 0 && bool(values.get("planner_started"))
                && !plan.linuxUser.isEmpty() && plan.userExists && !plan.directRootFallback
                && (plan.linuxUser.equals("root") || !plan.linuxHome.isEmpty());
        if (timedOut) {
            plan.issueKey = "root_execution_plan_timeout";
            plan.summary = "The chroot execution planner timed out.";
        } else if (!plan.usable) {
            plan.issueKey = "root_execution_plan_probe_failed";
            plan.summary = "The chroot execution planner did not return a usable plan.";
        } else if (plan.directRootFallback) {
            plan.issueKey = "root_execution_plan_direct_root_fallback";
            plan.summary = "User switching failed, so the planner selected direct root fallback.";
        } else if (plan.userSwitchMethod.equals(METHOD_SU)) {
            plan.issueKey = "root_execution_plan_su_fallback";
            plan.summary = "runuser was not usable, so the planner selected su fallback.";
        } else if (plan.userSwitchMethod.equals(METHOD_RUNUSER)) {
            plan.issueKey = "root_execution_plan_runuser";
            plan.summary = "The planner selected runuser with the first usable shell.";
        } else {
            plan.issueKey = "root_execution_plan_ok";
            plan.summary = "The planner selected a usable root/chroot executor.";
        }
        return plan;
    }

    JSONObject toJson() throws JSONException {
        JSONObject value = new JSONObject();
        value.put("environment", environment);
        value.put("chroot", chrootName);
        value.put("linux_user", linuxUser);
        value.put("linux_home", linuxHome);
        value.put("selected_shell", selectedShell);
        value.put("selected_shell_name", shellName);
        value.put("user_switch_method", userSwitchMethod);
        value.put("usable", usable);
        value.put("issue_key", issueKey);
        value.put("summary", summary);
        value.put("direct_root_fallback", directRootFallback);
        value.put("zsh_available", zshAvailable);
        value.put("bash_available", bashAvailable);
        value.put("sh_available", shAvailable);
        value.put("user_exists", userExists);
        value.put("runuser_available", runuserAvailable);
        value.put("runuser_works", runuserWorks);
        value.put("su_available", suAvailable);
        value.put("su_works", suWorks);
        value.put("rejected_shell_candidates", rejectedShellCandidates);
        value.put("rejected_user_switch_candidates", rejectedUserSwitchCandidates);
        return value;
    }

    String plainText() {
        return "Executor plan: " + environment + (chrootName.isEmpty() ? "" : " / " + chrootName)
                + (linuxUser.isEmpty() ? "" : " / " + linuxUser)
                + "\nHome: " + linuxHome
                + "\nShell: " + selectedShell
                + "\nUser switch: " + userSwitchMethod
                + "\nRejected shells: " + rejectedShellCandidates
                + "\nRejected user switches: " + rejectedUserSwitchCandidates
                + "\nSummary: " + summary;
    }

    static String markerPrefix() { return "__DEVTOOL_EXEC_PLAN__"; }

    private static Map<String, String> parse(String output) {
        Map<String, String> values = new LinkedHashMap<>();
        if (output == null) return values;
        for (String line : output.split("\\R")) {
            String clean = line.trim();
            if (!clean.startsWith(markerPrefix())) continue;
            String payload = clean.substring(markerPrefix().length());
            int equals = payload.indexOf('=');
            if (equals <= 0) continue;
            String key = payload.substring(0, equals).trim().toLowerCase(Locale.ROOT);
            String value = payload.substring(equals + 1).trim();
            if (!key.isEmpty()) values.put(key, value);
        }
        return values;
    }

    private static boolean bool(String value) {
        String clean = clean(value).toLowerCase(Locale.ROOT);
        return clean.equals("1") || clean.equals("true") || clean.equals("yes") || clean.equals("ok");
    }

    private static String first(String value, String fallback) {
        String clean = clean(value);
        return clean.isEmpty() ? fallback : clean;
    }

    private static String nameOf(String shell) {
        String clean = clean(shell);
        int slash = clean.lastIndexOf('/');
        return slash >= 0 ? clean.substring(slash + 1) : clean;
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
