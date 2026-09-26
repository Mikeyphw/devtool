from pathlib import Path
from devtool.models import ProjectConfig
from devtool.android.variants import resolve_android_variant, resolve_semantic_tasks, variant_requires_release_signing

def cfg(variant="debug", android=None):
    return ProjectConfig(repo_root=Path("."), kind="android", runner="android", variant=variant, runner_options={"android": android or {}})

def test_debug_profile_defaults():
    p=resolve_android_variant(cfg())
    assert (p.name,p.build_type,p.signing,p.release_gates)==("debug","debug","debug",False)

def test_release_profile_is_policy_driven():
    c=cfg("playRelease", {"variant_profiles":{"playRelease":{"build_type":"release","flavor":"play","signing":"production","signing_profile":"play","release_gates":True}}})
    p=resolve_android_variant(c)
    assert p.flavor=="play" and p.signing_profile=="play" and variant_requires_release_signing(c)

def test_semantic_tasks_follow_selected_variant():
    c=cfg("demoDebug")
    assert resolve_semantic_tasks(c,["assemble","unitTest","lint","bundle","connectedAndroidTest",":lib:check"]) == [":app:assembleDemoDebug",":app:testDemoDebugUnitTest",":app:lintDemoDebug",":app:bundleDemoDebug",":app:connectedDemoDebugAndroidTest",":lib:check"]

def test_merged_target_variant_android_values_are_profile_values():
    p=resolve_android_variant(cfg("benchmark", {"build_type":"release","signing":"debug","release_gates":False}))
    assert p.build_type=="release" and p.signing=="debug" and not p.release_gates

def test_release_command_defaults_to_release_without_active_variant():
    from devtool.android.release import release_options
    c=cfg("")
    assert release_options(c).variant == "release"

def test_variant_listing_includes_default_and_release():
    from devtool.android.variants import list_android_variant_profiles
    names=[p.name for p in list_android_variant_profiles(cfg(""))]
    assert names == ["debug", "release"]

def test_android_variants_table_alias_and_nested_signing_policy():
    c=cfg("playRelease", {"variants":{"playRelease":{"build_type":"release","flavor":"play","signing":{"policy":"production","profile":"play"},"release_gates":True}}})
    p=resolve_android_variant(c)
    assert (p.flavor,p.signing,p.signing_profile,p.release_gates)==("play","production","play",True)

def test_release_activation_merges_target_variant_android_options():
    from devtool.models import TargetConfig
    from devtool.android.variants import activate_android_variant
    c=cfg("")
    c.selected_target="app"
    c.targets={"app": TargetConfig(name="app", variants={"release":{"android":{"build_type":"release","signing":"production","signing_profile":"prod","deployable":True,"release_gates":True}}})}
    a=activate_android_variant(c,"release")
    p=resolve_android_variant(a)
    assert a.variant=="release" and (p.build_type,p.signing,p.signing_profile)==("release","production","prod")

def test_release_artifact_selection_prefers_selected_custom_variant(tmp_path):
    from devtool.android.release import AndroidReleaseOrchestrator
    c=cfg("playRelease")
    play=tmp_path/"app-playRelease.apk"; play.write_bytes(b"x")
    other=tmp_path/"app-release.apk"; other.write_bytes(b"x")
    payload={"items":[{"valid":True,"type":"apk","path":str(other),"variant":"release"},{"valid":True,"type":"apk","path":str(play),"variant":"playRelease"}]}
    got=AndroidReleaseOrchestrator._release_artifacts(c,payload)
    assert [x[0] for x in got]==[play.resolve()]


def test_semantic_tasks_support_configured_application_module():
    c=cfg("release", {"module":"mobile"})
    assert resolve_semantic_tasks(c,["assemble","lint"]) == [":mobile:assembleRelease",":mobile:lintRelease"]

def test_release_qualification_respects_profile_gate_policy():
    from devtool.android.release import AndroidReleaseOrchestrator
    c=cfg("benchmark", {"variants":{"benchmark":{"build_type":"release","signing":"debug","release_gates":False}}})
    try:
        AndroidReleaseOrchestrator(c).plan(overrides={"device_tests":"disabled"})
    except ValueError as exc:
        assert "not enabled for release qualification" in str(exc)
    else:
        raise AssertionError("release qualification unexpectedly accepted release_gates=false")

def test_explicit_debug_signing_policy_overrides_release_named_task():
    from devtool.android.variants import has_explicit_signing_policy
    c=cfg("benchmarkRelease", {"variants":{"benchmarkRelease":{"build_type":"release","signing":"debug","release_gates":False}}})
    assert has_explicit_signing_policy(c)
    assert not resolve_android_variant(c).requires_release_signing

def test_module_qualified_default_lint_is_not_misclassified_custom(monkeypatch):
    import devtool.android_integration as ai
    c=cfg("debug", {"module":"mobile"})
    monkeypatch.setattr(ai, "configured_tasks", lambda _c, action: [":mobile:lintDebug"] if action == "lint" else [])
    monkeypatch.setattr(ai, "_validation_changed_paths", lambda _c: [])
    tasks, reason, affected = ai.android_validation_lint_tasks(c)
    assert tasks == [":mobile:lintDebug"] and reason == "full-no-change-data" and affected == []

def test_release_artifact_selection_fails_closed_when_selected_variant_is_absent(tmp_path):
    from devtool.android.release import AndroidReleaseOrchestrator
    c=cfg("playRelease")
    named_release=tmp_path/"app-release.apk"; named_release.write_bytes(b"x")
    neutral=tmp_path/"artifact.apk"; neutral.write_bytes(b"x")
    payload={"items":[
        {"valid":True,"type":"apk","path":str(named_release)},
        {"valid":True,"type":"apk","path":str(neutral)},
    ]}
    assert AndroidReleaseOrchestrator._release_artifacts(c,payload) == []


def test_resumable_validation_defaults_consume_profile_module():
    # Guard the remaining validation path that used to reconstruct the variant
    # independently from config.variant and emitted root-project Android tasks.
    import inspect
    from devtool.project_runners import AndroidGradleRunner
    source=inspect.getsource(AndroidGradleRunner._build_resumable_validation_specs)
    assert "variant_profile = resolve_android_variant(config)" in source
    assert 'module_prefix = f":{variant_profile.module}:"' in source
    assert 'f"{module_prefix}compile{variant_title}Sources"' in source
    assert 'f"{module_prefix}process{variant_title}Resources"' in source

def test_signing_plan_uses_profile_not_release_word(monkeypatch):
    import devtool.android.signing as s
    c=cfg("prod", {"variants":{"prod":{"build_type":"benchmark","signing":"production","signing_profile":"release"}}})
    monkeypatch.setattr(s, "validate_signing_profile_for_tasks", lambda *a, **k: type("P",(),{"mode":"devtool","name":"release","store_password_env":"","store_password_fd":None,"store_password_command":"","store_password_stdin":False,"key_password_env":"","key_password_fd":None,"key_password_command":"","key_password_stdin":False,"allow_prompt":False})())
    md=s.signing_plan_metadata(c,[":app:assembleProd"])
    assert md["android_signing_required"] is True

def test_execution_boundary_trusts_canonical_signing_metadata(monkeypatch):
    import devtool.android.signing as s
    c=cfg("prod", {"variants":{"prod":{"build_type":"benchmark","signing":"production"}}})
    profile=type("P",(),{"mode":"project"})()
    monkeypatch.setattr(s,"validate_signing_profile_for_tasks",lambda *a,**k: profile)
    out=s.signing_environment_for_execution(c,{"android_signing_required":True,"android_signing_tasks":[":app:assembleProd"]},env={})
    assert out == {}

def test_runner_does_not_reinfer_release_signing_from_task_names():
    import inspect, devtool.runners as r
    src=inspect.getsource(r.run_specs)
    assert "release_markers =" not in src
    assert "Infer release intent from the final Gradle argv" not in src

def test_release_qualification_uses_semantic_android_tasks():
    from pathlib import Path
    text=(Path(__file__).parents[1]/"devtool"/"release_qualification.py").read_text()
    assert '"build": ["assemble", "verifyReleaseApk"]' in text
    assert '"test": ["unitTest"]' in text
    assert '"lint": ["lint"]' in text
