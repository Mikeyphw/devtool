package io.github.mikeceead.devtoolhud;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

final class RepositoryPathResolution {
    static final String STATUS_PASS = "pass";
    static final String STATUS_WARN = "warn";
    static final String STATUS_FAIL = "fail";

    String repositoryId = "";
    String status = STATUS_PASS;
    String issueKey = "repo_path_metadata_ok";
    String title = "Repository metadata looks consistent";
    String summary = "The stored repository path matches the selected execution namespace.";
    String recommendation = "No metadata repair is needed before refreshing this repository.";
    String environment = "";
    String chrootName = "";
    String linuxUser = "";
    String configuredPath = "";
    String canonicalPath = "";
    String hostPath = "";
    String gitRoot = "";
    String detectedPathFamily = "unknown";
    String expectedPathFamily = "unknown";
    final List<RepairAction> actions = new ArrayList<>();

    boolean hasRepairActions() { return !actions.isEmpty(); }

    RepairAction primaryAction() { return actions.isEmpty() ? null : actions.get(0); }

    JSONObject toJson() throws JSONException {
        JSONObject value = new JSONObject();
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
        value.put("git_root", gitRoot);
        value.put("detected_path_family", detectedPathFamily);
        value.put("expected_path_family", expectedPathFamily);
        JSONArray repairs = new JSONArray();
        for (RepairAction action : actions) repairs.put(action.toJson());
        value.put("safe_repairs", repairs);
        return value;
    }

    String plainText() {
        StringBuilder text = new StringBuilder();
        text.append(title).append('\n');
        text.append(summary).append('\n');
        text.append("Issue: ").append(issueKey).append('\n');
        text.append("Environment: ").append(environment).append('\n');
        if (!chrootName.isEmpty()) text.append("Chroot: ").append(chrootName).append('\n');
        if (!linuxUser.isEmpty()) text.append("Linux user: ").append(linuxUser).append('\n');
        text.append("Configured path: ").append(configuredPath).append('\n');
        text.append("Canonical path: ").append(canonicalPath).append('\n');
        if (!hostPath.isEmpty()) text.append("Host path: ").append(hostPath).append('\n');
        if (!gitRoot.isEmpty()) text.append("Git root: ").append(gitRoot).append('\n');
        text.append("Detected family: ").append(detectedPathFamily).append('\n');
        text.append("Expected family: ").append(expectedPathFamily).append('\n');
        text.append("Recommendation: ").append(recommendation).append('\n');
        if (!actions.isEmpty()) {
            text.append("Safe repair actions:\n");
            for (RepairAction action : actions) {
                text.append("- ").append(action.label).append(": ").append(action.description).append('\n');
            }
        }
        return text.toString();
    }

    static final class RepairAction {
        static final String KIND_RECLASSIFY_TERMUX = "reclassify_termux";
        static final String KIND_RECLASSIFY_CHROOT = "reclassify_chroot";
        static final String KIND_RECLASSIFY_ROOT = "reclassify_root";
        static final String KIND_UPDATE_PATH = "update_path";
        static final String KIND_USE_DISCOVERED_GIT_ROOT = "use_discovered_git_root";
        static final String KIND_CHANGE_LINUX_USER = "change_linux_user";
        static final String KIND_CHANGE_CHROOT_NAME = "change_chroot_name";

        String actionId = "";
        String kind = "";
        String label = "";
        String description = "";
        String environment = "";
        String chrootName = "";
        String linuxUser = "";
        String path = "";
        String hostPath = "";
        boolean primary;
        boolean safe = true;

        RepositoryLocation location() {
            if (environment.equals("chroot")) return RepositoryLocation.chroot(chrootName, linuxUser, path);
            if (environment.equals("root")) return RepositoryLocation.root(path);
            return RepositoryLocation.termux(path);
        }

        JSONObject toJson() throws JSONException {
            JSONObject value = new JSONObject();
            value.put("action_id", actionId);
            value.put("kind", kind);
            value.put("label", label);
            value.put("description", description);
            value.put("environment", environment);
            value.put("chroot", chrootName);
            value.put("linux_user", linuxUser);
            value.put("path", path);
            value.put("host_path", hostPath);
            value.put("primary", primary);
            value.put("safe", safe);
            return value;
        }
    }
}
