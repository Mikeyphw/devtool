"""Wave B: static Android module include grammar; never execute Gradle."""
from pathlib import Path
import pytest
from devtool.actions_android_model import _include_names, discover_modules
from devtool.github_actions import GitHubActionsError

@pytest.mark.parametrize('text, names', [
    ('include(":app", ":core",)', {":app", ":core"}),
    ("""include(
 ":app",
 ":core",
 // Kotlin comment stripped at callsite
)""", {":app", ":core"}),
    ('include(":app",)', {":app"}),
    ('include(":app", ":core")', {":app", ":core"}),
    ("include ':app', ':core'", {":app", ":core"}),
])
def test_literal_module_includes(text, names):
    # The discoverer strips comments before parsing; direct parser fixtures do likewise.
    from devtool.actions_android_model import _strip_comments
    assert _include_names(_strip_comments(text)) == names

@pytest.mark.parametrize('text', [
    'include(":app", , ":core")',
    'include(, ":app")',
    'include(":app",,)',
    'include(":app",,,)',
    'include(":app", modulesFromEnv)',
    'include(":app", project(":lib"))',
    'include(":app" + suffix)',
    'include(":app",',
    'include()',
])
def test_nonliteral_or_malformed_include_refused(text):
    with pytest.raises(GitHubActionsError):
        _include_names(text)

def test_realistic_kotlin_multimodule_trailing_comma(tmp_path):
    root=Path(tmp_path)
    (root/'settings.gradle.kts').write_text("""rootProject.name = "sample"
include(
    ":app",
    ":core-model",
    ":persistence",
)
""")
    app=root/'app';app.mkdir()
    (app/'build.gradle.kts').write_text("""plugins { id("com.android.application") }
android { defaultConfig { applicationId = "com.example.sample" } }
""")
    modules=discover_modules(root)
    assert list(modules)==['app']
    assert modules['app']['application_id']=='com.example.sample'
