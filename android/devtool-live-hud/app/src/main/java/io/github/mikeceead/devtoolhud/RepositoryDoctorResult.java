package io.github.mikeceead.devtoolhud;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

final class RepositoryDoctorResult {
    static final String UNKNOWN = "unknown";
    long checkedAtMs;
    String repositoryId = "";
    String status = UNKNOWN;
    String issueKey = "repo_doctor_not_run";
    String title = "Repository doctor has not run";
    String summary = "Run the doctor to verify path namespace, executor, shell and Git readiness.";
    String recommendation = "Run Doctor before retrying Git actions.";
    String environment = "";
    String chrootName = "";
    String linuxUser = "";
    String configuredPath = "";
    String canonicalPath = "";
    String hostPath = "";
    String commandPath = "";
    int exitCode = -1;
    boolean timedOut;
    boolean pathExists;
    boolean gitDirExists;
    boolean gitAvailable;
    boolean gitCommandWorks;
    boolean shAvailable;
    boolean bashAvailable;
    boolean zshAvailable;
    boolean userExists;
    boolean runuserAvailable;
    boolean runuserWorks;
    boolean suAvailable;
    boolean suWorks;
    boolean executorReachable;
    String selectedShell = "";
    String userSwitchMethod = "";
    String rejectedShellCandidates = "";
    String rejectedUserSwitchCandidates = "";
    boolean directRootFallback;
    String outputPreview = "";
    final Map<String, String> evidence = new LinkedHashMap<>();

    static RepositoryDoctorResult passive(RepositoryRecord repository) {
        RepositoryDoctorResult result = new RepositoryDoctorResult();
        result.checkedAtMs = 0L;
        result.populate(repository);
        result.status = UNKNOWN;
        result.issueKey = "repo_doctor_not_run";
        result.title = "Repository doctor has not run";
        result.summary = "No active repository doctor result is available yet.";
        result.recommendation = "Run Doctor from the repository detail page.";
        return result;
    }

    void populate(RepositoryRecord repository) {
        if (repository == null) return;
        RepositoryLocation location = repository.location();
        repositoryId = clean(repository.repositoryId);
        environment = clean(location.environment);
        chrootName = clean(location.chrootName);
        linuxUser = clean(location.linuxUser);
        configuredPath = clean(repository.configuredPath);
        canonicalPath = clean(repository.canonicalPath);
        hostPath = clean(repository.hostPath);
        commandPath = commandPath(location);
    }

    void ingest(Map<String, String> values) {
        evidence.clear();
        evidence.putAll(values);
        pathExists = bool(values.get("path_exists"));
        gitDirExists = bool(values.get("git_dir_exists"));
        gitAvailable = bool(values.get("git_available"));
        gitCommandWorks = bool(values.get("git_c_works"));
        shAvailable = bool(values.get("sh_available"));
        bashAvailable = bool(values.get("bash_available"));
        zshAvailable = bool(values.get("zsh_available"));
        userExists = bool(values.get("user_exists"));
        runuserAvailable = bool(values.get("runuser_available"));
        runuserWorks = bool(values.get("runuser_works"));
        suAvailable = bool(values.get("su_available"));
        suWorks = bool(values.get("su_works"));
        executorReachable = bool(values.get("doctor_started")) || !values.isEmpty();
        selectedShell = clean(values.get("selected_shell"));
        userSwitchMethod = clean(values.get("user_switch_method"));
        rejectedShellCandidates = clean(values.get("rejected_shell_candidates"));
        rejectedUserSwitchCandidates = clean(values.get("rejected_user_switch_candidates"));
        directRootFallback = userSwitchMethod.equals("direct-root")
                && !linuxUser.isEmpty() && !linuxUser.equals("root");
        outputPreview = clean(values.get("output_preview"));
        classify();
    }

    private void classify() {
        if (timedOut) {
            fail("repo_doctor_timeout", "Repository doctor timed out",
                    "The executor accepted the doctor command but did not finish in time.",
                    "Check for stuck root/chroot process state before retrying Git operations.");
            return;
        }
        if (!executorReachable || exitCode == 69 || lower(outputPreview).contains("chrootctl unavailable")) {
            fail("repo_chroot_unavailable", "Repository executor is unreachable",
                    "The doctor could not reach the selected root/chroot execution environment.",
                    "Verify chrootctl, the configured chroot name and root permissions.");
            return;
        }
        if (environment.equals("chroot") && linuxUser.isEmpty()) {
            fail("repo_chroot_user_missing", "Chroot Linux user is not configured",
                    "The selected chroot repository has no proven Linux executor identity.",
                    "Configure the repository Linux user explicitly before running Git or validation commands.");
            return;
        }
        if (environment.equals("chroot") && canonicalPath.startsWith(RepositoryLocation.TERMUX_HOME)) {
            fail("repo_path_wrong_namespace", "Repository path uses the wrong namespace",
                    "This repository is configured as chroot, but its path looks like a Termux host path.",
                    "Re-add it as Termux or change the repository path to the path visible inside the chroot, usually /home/<user>/...");
            return;
        }
        if (!environment.equals("chroot") && canonicalPath.startsWith("/home/") && hostPath.isEmpty()) {
            warn("repo_path_maybe_chroot_namespace", "Repository path may belong to a chroot",
                    "The path looks like a Linux home directory while the repository is not configured as chroot.",
                    "If Git fails, reclassify the repository as chroot or use the Termux-visible path.");
            return;
        }
        if (!pathExists) {
            fail("repo_doctor_path_missing", "Repository path does not exist",
                    "The configured path was not found in the selected execution namespace.",
                    "Use the browser to select the path visible to this environment, or reclassify the repository.");
            return;
        }
        if (!gitDirExists) {
            fail("repo_git_root_missing", "Directory is not a Git repository",
                    "The path exists, but .git or git rev-parse did not confirm a repository root.",
                    "Use the discovered Git root or add the repository folder that contains .git.");
            return;
        }
        if (!gitAvailable) {
            fail("repo_doctor_git_missing", "Git is not available in this environment",
                    "The selected executor can reach the path, but command -v git failed.",
                    "Install Git in Termux/chroot or choose an environment that has Git.");
            return;
        }
        if (!shAvailable && !bashAvailable && !zshAvailable) {
            fail("repo_doctor_shell_missing", "No usable shell found",
                    "The selected namespace did not report sh, bash or zsh.",
                    "Install a shell in the chroot or use a different execution environment.");
            return;
        }
        if (environment.equals("chroot") && !linuxUser.isEmpty() && !linuxUser.equals("root")) {
            if (!userExists) {
                fail("repo_chroot_user_missing", "Configured chroot user is missing",
                        "The repository path is checked as chroot, but the Linux user does not exist there.",
                        "Change the repository Linux user or add the user inside the chroot.");
                return;
            }
            if (runuserAvailable && !runuserWorks && !suWorks) {
                fail("repo_doctor_runuser_failed", "User switch failed before Git started",
                        "runuser was found, but switching to the configured Linux user failed and no su fallback worked.",
                        "Use root fallback for this chroot, fix runuser/util-linux, or change the repository user.");
                return;
            }
            if (!runuserAvailable && !suWorks) {
                fail("repo_user_switch_fallback_missing", "No user-switch method worked",
                        "Neither runuser nor su could switch to the configured Linux user.",
                        "Install or fix runuser/su, or configure a different Linux user. Devtool will not fall back to root.");
                return;
            }
        }
        if (!gitCommandWorks) {
            fail("repo_git_command_failed", "Git could not inspect this path",
                    "Git exists, but git -C against the configured path failed.",
                    "Check permissions, worktree health and the selected execution namespace.");
            return;
        }
        pass("repo_doctor_ok", "Repository executor is ready",
                "Path, Git, shell and executor checks passed for this repository.",
                "Retry the Git operation. If it still fails, inspect the operation log.");
    }

    JSONObject toJson() throws JSONException {
        JSONObject value = new JSONObject();
        value.put("checked_at_ms", checkedAtMs);
        value.put("repository_id", repositoryId);
        value.put("status", status);
        value.put("issue_key", issueKey);
        value.put("title", title);
        value.put("summary", summary);
        value.put("recommendation", recommendation);
        value.put("environment", environment);
        value.put("chroot", chrootName);
        value.put("linux_user", linuxUser);
        value.put("configured_path", configuredPath);
        value.put("canonical_path", canonicalPath);
        value.put("host_path", hostPath);
        value.put("command_path", commandPath);
        value.put("exit_code", exitCode);
        value.put("timed_out", timedOut);
        value.put("path_exists", pathExists);
        value.put("git_dir_exists", gitDirExists);
        value.put("git_available", gitAvailable);
        value.put("git_c_works", gitCommandWorks);
        value.put("sh_available", shAvailable);
        value.put("bash_available", bashAvailable);
        value.put("zsh_available", zshAvailable);
        value.put("shells", joinFlags("sh", shAvailable, "bash", bashAvailable, "zsh", zshAvailable));
        value.put("user_exists", userExists);
        value.put("runuser_available", runuserAvailable);
        value.put("runuser_works", runuserWorks);
        value.put("su_available", suAvailable);
        value.put("su_works", suWorks);
        value.put("selected_shell", selectedShell);
        value.put("user_switch_method", userSwitchMethod);
        value.put("direct_root_fallback", directRootFallback);
        value.put("rejected_shell_candidates", rejectedShellCandidates);
        value.put("rejected_user_switch_candidates", rejectedUserSwitchCandidates);
        value.put("output_preview", outputPreview);
        return value;
    }

    String plainText() {
        return title + "\n"
                + "Status: " + status + "\n"
                + "Issue: " + issueKey + "\n"
                + "Environment: " + environment + (chrootName.isEmpty() ? "" : " / " + chrootName)
                + (linuxUser.isEmpty() ? "" : " / " + linuxUser) + "\n"
                + "Path: " + commandPath + "\n"
                + "Summary: " + summary + "\n"
                + "Recommendation: " + recommendation + "\n"
                + "Executor plan: shell=" + selectedShell + ", user_switch=" + userSwitchMethod
                + (directRootFallback ? " (direct root fallback)" : "") + "\n"
                + "Checks: path=" + yesNo(pathExists) + ", git_root=" + yesNo(gitDirExists)
                + ", git=" + yesNo(gitAvailable) + ", git_c=" + yesNo(gitCommandWorks)
                + ", runuser=" + yesNo(runuserWorks) + ", su=" + yesNo(suWorks);
    }

    private void pass(String key, String title, String summary, String recommendation) {
        this.status = "pass";
        this.issueKey = key;
        this.title = title;
        this.summary = summary;
        this.recommendation = recommendation;
    }

    private void warn(String key, String title, String summary, String recommendation) {
        this.status = "warn";
        this.issueKey = key;
        this.title = title;
        this.summary = summary;
        this.recommendation = recommendation;
    }

    private void fail(String key, String title, String summary, String recommendation) {
        this.status = "fail";
        this.issueKey = key;
        this.title = title;
        this.summary = summary;
        this.recommendation = recommendation;
    }

    private static String commandPath(RepositoryLocation location) {
        if (location.environment.equals("native-termux") && !location.hostPath.isEmpty()) return location.hostPath;
        return location.path;
    }

    private static boolean bool(String value) {
        String clean = clean(value).toLowerCase(Locale.ROOT);
        return clean.equals("1") || clean.equals("true") || clean.equals("yes") || clean.equals("ok");
    }

    private static String joinFlags(String first, boolean firstValue, String second, boolean secondValue,
                                    String third, boolean thirdValue) {
        return first + "=" + yesNo(firstValue) + ", " + second + "=" + yesNo(secondValue)
                + ", " + third + "=" + yesNo(thirdValue);
    }

    private static String yesNo(boolean value) { return value ? "yes" : "no"; }
    private static String lower(String value) { return clean(value).toLowerCase(Locale.ROOT); }
    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
