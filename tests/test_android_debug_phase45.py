from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase45_support_bundle_v2_exports_debug_cockpit_sections() -> None:
    support = read("SupportBundle.java")
    for symbol in (
        'root.put("schema", 2)',
        'root.put("bundle_version", "support_bundle_v2")',
        'root.put("summary", summary(app))',
        'root.put("latest_probe_results", latestProbeResults(app))',
        'root.put("debug_events", debugEvents(app, token))',
        'root.put("run_command_trace", runCommandTrace(app, token))',
        'root.put("repo_doctor_results", repoDoctorResults(app))',
        'root.put("repo_execution_plan", repoExecutionPlan(app))',
        'root.put("repo_path_resolution", repoPathResolution(app))',
        'root.put("repo_queue_health", repoQueueHealth(app))',
        'root.put("recent_git_operations", recentGitOperations(app, token))',
        'root.put("validation_health", validationHealth(app))',
        'root.put("notification_health", notificationHealth(app))',
        'root.put("redaction_self_test", redactionSelfTest(token))',
    ):
        assert symbol in support
    assert "Support Bundle v2 exports debug cockpit evidence as bounded redacted JSON sections" in support


def test_phase45_debug_events_and_run_trace_are_bounded_and_redacted() -> None:
    support = read("SupportBundle.java")
    for symbol in (
        "MAX_DEBUG_EVENTS = 160",
        "MAX_TRACE_EVENTS = 96",
        "recentDebugEvents(MAX_DEBUG_EVENTS)",
        "debugEventsForRun(run.runId, MAX_TRACE_EVENTS)",
        "debugEvent(DebugEventRecord event, String token, boolean includeDetails)",
        "RunCommandTrace.order(event.eventType)",
        'item.put("checkpoint_order", RunCommandTrace.order(event.eventType))',
        'value.put("raw_log_output_included", false)',
        "truncate(redact(event.message, token))",
        "truncate(redact(event.detailJson, token))",
    ):
        assert symbol in support


def test_phase45_repo_sections_cover_doctor_path_queue_execution_and_git_trace() -> None:
    support = read("SupportBundle.java")
    for symbol in (
        "RepositoryDoctor.supportSummary(context, repository)",
        "RepositoryPathResolver.supportSummary(repository)",
        "RepoQueueRecovery.supportSummary(context, repository)",
        "GitOperationTrace.supportSummary(context, operation, token)",
        "RepoQueueRecovery.recoveryHint(operation)",
        'plan.put("selected_shell"',
        'plan.put("user_switch_method"',
        'plan.put("runuser_available"',
        'plan.put("runuser_works"',
        'plan.put("rejected_shell_candidates"',
        'plan.put("rejected_user_switch_candidates"',
        "MAX_BUNDLE_REPOSITORIES = 16",
        "MAX_RECENT_GIT_OPERATIONS = 24",
    ):
        assert symbol in support
    for forbidden in ("git reset", "git clean", "rm -rf", "deleteRepository"):
        assert forbidden not in support


def test_phase45_validation_notification_and_probe_health_are_exported() -> None:
    support = read("SupportBundle.java")
    for symbol in (
        "DebugWorkbenchHealth.assess(context)",
        "DebugWorkbenchHealth.supportSummary(context)",
        "DebugGuidedFix.supportSummary(context)",
        "BridgeRoundtripProbe.supportSummary(context)",
        "RootBridgeManager.state(context)",
        "BridgeTokenManager.isReady(context)",
        "ValidationStore.active(context)",
        "ValidationStore.latest(context)",
        "NotificationManager manager = context.getSystemService(NotificationManager.class)",
        'value.put("notifications_enabled"',
        'value.put("overlay_permission"',
        'value.put("safe_actions_only", true)',
    ):
        assert symbol in support


def test_phase45_redaction_self_test_covers_tokens_auth_urls_secrets_and_private_keys() -> None:
    support = read("SupportBundle.java")
    redactor = read("SupportRedactor.java")
    for symbol in (
        "redactionSelfTest(String token)",
        "SupportRedactor.redact(sample, token)",
        'value.put("bridge_token_redacted"',
        'value.put("auth_header_redacted"',
        'value.put("url_credentials_redacted"',
        'value.put("secret_assignment_redacted"',
        'value.put("private_key_redacted"',
        'bearerValue = "Bearer " + "abcdef"',
        'credentialPair = "user" + ":" + "pass"',
        'secretValue = "hunter" + "2"',
        'privateKeyMarker = "BEGIN " + "PRIVATE KEY"',
    ):
        assert symbol in support
    for symbol in ("HTTPS_CREDENTIALS", "BEARER", "KEY_VALUE_SECRET", "PRIVATE_KEY_BLOCK"):
        assert symbol in redactor
