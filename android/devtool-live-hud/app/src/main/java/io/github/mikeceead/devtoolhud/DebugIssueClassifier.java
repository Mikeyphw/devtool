package io.github.mikeceead.devtoolhud;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

final class DebugIssueClassifier {
    private DebugIssueClassifier() {}

    static String classifyMessage(String message) {
        String lower = clean(message).toLowerCase(java.util.Locale.ROOT);
        if (lower.contains("git_operation_queued")) return "git_operation_queued";
        if (lower.contains("git_operation_started")) return "git_operation_started";
        if (lower.contains("repo_execution_plan_built")) return "repo_execution_plan_built";
        if (lower.contains("repo_path_confirmed")) return "repo_path_confirmed";
        if (lower.contains("git_binary_confirmed")) return "git_binary_confirmed";
        if (lower.contains("git_command_started")) return "git_command_started";
        if (lower.contains("git_output_received")) return "git_output_received";
        if (lower.contains("git_operation_finished")) return "git_operation_finished";
        if (lower.contains("git_operation_failed")) return "git_operation_failed";
        if (lower.contains("repo_path_confirmation_failed")) return "repo_path_confirmation_failed";
        if (lower.contains("git_binary_confirmation_failed")) return "git_binary_confirmation_failed";
        if (lower.contains("repo_git_root_confirmation_failed")) return "repo_git_root_confirmation_failed";
        if (lower.contains("git trace preflight") && lower.contains("before git started")) return "git_operation_preflight_failed";
        if (lower.contains("repo_queue_health_failed")) return "repo_queue_health_failed";
        if (lower.contains("repo_queue_health_warn")) return "repo_queue_health_warn";
        if (lower.contains("repo_operation_mark_interrupted")) return "repo_operation_mark_interrupted";
        if (lower.contains("repo_orphan_terminate_requested")) return "repo_orphan_terminate_requested";
        if (lower.contains("repo_operation_retry_same_plan_requested")) return "repo_operation_retry_same_plan_requested";
        if (lower.contains("repo_operation_retry_after_path_repair_requested")) return "repo_operation_retry_after_path_repair_requested";
        if (lower.contains("repo_operation_retry_unavailable")) return "repo_operation_retry_unavailable";
        if (lower.contains("orphaned git operation") || lower.contains("orphaned repository operation")) return "repo_operation_orphaned";
        if (lower.contains("marked interrupted")) return "repo_operation_mark_interrupted";
        if (lower.contains("retry requested after repository path repair")) return "repo_operation_retry_after_path_repair_requested";
        if (lower.contains("repo_path_repair_applied")) return "repo_path_repair_applied";
        if (lower.contains("repo_termux_path_saved_as_chroot")) return "repo_termux_path_saved_as_chroot";
        if (lower.contains("repo_chroot_path_saved_as_termux_or_root")) return "repo_chroot_path_saved_as_termux_or_root";
        if (lower.contains("repo_root_path_saved_as_termux")) return "repo_root_path_saved_as_termux";
        if (lower.contains("repo_termux_path_saved_as_root")) return "repo_termux_path_saved_as_root";
        if (lower.contains("repo_discovered_git_root_available")) return "repo_discovered_git_root_available";
        if (lower.contains("repo_invalid_home_expansion")) return "repo_invalid_home_expansion";
        if (lower.contains("repo_missing_path_repair_available")) return "repo_missing_path_repair_available";
        if (lower.contains("termux path saved as chroot")) return "repo_termux_path_saved_as_chroot";
        if (lower.contains("linux home path") && lower.contains("chroot metadata")) return "repo_chroot_path_saved_as_termux_or_root";
        if (lower.contains("discovered git root")) return "repo_discovered_git_root_available";
        if (lower.contains("root_execution_plan_direct_root_fallback") || lower.contains("direct root fallback")) return "root_execution_plan_direct_root_fallback";
        if (lower.contains("root_execution_plan_su_fallback") || lower.contains("su fallback")) return "root_execution_plan_su_fallback";
        if (lower.contains("root_execution_plan_probe_failed")) return "root_execution_plan_probe_failed";
        if (lower.contains("root_execution_plan_timeout")) return "root_execution_plan_timeout";
        if (lower.contains("repo_path_wrong_namespace") || lower.contains("wrong namespace")) return "repo_path_wrong_namespace";
        if (lower.contains("repo_doctor_path_missing")) return "repo_doctor_path_missing";
        if (lower.contains("repo_doctor_runuser_failed") || (lower.contains("runuser") && lower.contains("before git"))) return "repo_doctor_runuser_failed";
        if (lower.contains("repo_doctor_git_missing")) return "repo_doctor_git_missing";
        if (lower.contains("repo_doctor_shell_missing") || lower.contains("no usable shell")) return "repo_doctor_shell_missing";
        if (lower.contains("runuser")) return "repo_runuser_failed";
        if (lower.contains("chrootctl unavailable") || (lower.contains("chroot") && lower.contains("unavailable"))) return "repo_chroot_unavailable";
        if (lower.contains("zsh") && (lower.contains("not found") || lower.contains("no such file"))) return "repo_shell_missing";
        if (lower.contains("git") && (lower.contains("not found") || lower.contains("unavailable"))) return "repo_git_missing";
        if (lower.contains("repository directory does not exist") || lower.contains("path does not exist")
                || lower.contains("does not exist") || lower.contains("no such file or directory")) return "path_missing";
        if (lower.contains("bridge roundtrip passed")) return "bridge_roundtrip_ok";
        if (lower.contains("roundtrip") && lower.contains("callback") && lower.contains("did not reach")) return "bridge_roundtrip_callback_missing";
        if (lower.contains("roundtrip") && lower.contains("stdout marker")) return "bridge_roundtrip_marker_missing";
        if (lower.contains("roundtrip") && lower.contains("wrapper checkpoint never reached")) return "bridge_roundtrip_wrapper_missing";
        if (lower.contains("token") && (lower.contains("invalid") || lower.contains("mismatch") || lower.contains("rejected"))) return "bridge_token_mismatch";
        if ((lower.contains("run_command") && lower.contains("permission")) || lower.contains("permission is not granted")) return "termux_run_command_permission_missing";
        if (lower.contains("termux is not installed")) return "termux_missing";
        if (lower.contains("shell did not confirm") || lower.contains("wrapper never started")
                || lower.contains("no wrapper checkpoint")) return "termux_wrapper_never_started";
        if (lower.contains("bridge token or component markers are missing")
                || lower.contains("token_markers_missing")) return "termux_wrapper_markers_missing";
        if (lower.contains("workdir missing") || lower.contains("workdir missing or unreadable")) return "termux_workdir_missing";
        if (lower.contains("command not found before exec") || lower.contains("command_not_found")) return "termux_command_not_found_before_exec";
        if (lower.contains("starting requested command") || lower.contains("exec_starting")) return "termux_exec_starting";
        if (lower.contains("command exited with code")) return "termux_wrapper_exit_code_recorded";
        if (lower.contains("first authenticated bridge progress")) return "first_progress_received";
        if (lower.contains("stale") || lower.contains("out-of-order")) return "stale_bridge_update";
        if (lower.contains("timeout") || lower.contains("timed out")) return "timeout";
        if (lower.contains("cancel")) return "cancelled";
        if (lower.contains("failed") || lower.contains("error") || lower.contains("exception")) return "generic_failure";
        return "generic_event";
    }

    static String fingerprint(String category, String eventType, String message, String detailJson) {
        String source = clean(category).toLowerCase(java.util.Locale.ROOT)
                + "|" + clean(eventType).toUpperCase(java.util.Locale.ROOT)
                + "|" + classifyMessage(clean(message) + " " + clean(detailJson));
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(source.getBytes(StandardCharsets.UTF_8));
            StringBuilder text = new StringBuilder("dbg-");
            for (int i = 0; i < 8 && i < hash.length; i++) {
                text.append(String.format(java.util.Locale.ROOT, "%02x", hash[i] & 0xff));
            }
            return text.toString();
        } catch (Exception ignored) {
            return "dbg-fallback-" + Integer.toHexString(source.hashCode());
        }
    }

    private static String clean(String value) { return value == null ? "" : value.trim(); }
}
