from __future__ import annotations
from pathlib import Path
import pytest
from devtool.android.environment import EnvironmentView, build_android_environment_request
from devtool.models import ProjectConfig

def request(tmp_path:Path, requested):
    (tmp_path/"settings.gradle").write_text("rootProject.name='test'\n")
    (tmp_path/"build.gradle").write_text("plugins { id 'com.android.application' version '8.9.0' apply false }\nandroid { compileSdk 36 }\n")
    config=ProjectConfig(repo_root=tmp_path,project_root=str(tmp_path),kind="android",runner="android",runner_options={"android":{"platform":requested}})
    return build_android_environment_request(config,action="build",env_view=EnvironmentView.from_config(config,env={}))

@pytest.mark.parametrize("value", ["invalid", "android-", "android-nope", "android-0", "-1", "3.6", "android-36-extra", "android- 36", "0", "android-+36"])
def test_malformed_explicit_platform_never_falls_back(tmp_path, value):
    with pytest.raises(ValueError,match="invalid explicit Android platform override"):
        request(tmp_path,value)

@pytest.mark.parametrize("value,wanted", [("android-35",35),("35",35),("auto",36),("",36)])
def test_supported_platform_overrides_still_resolve(tmp_path,value,wanted):
    assert request(tmp_path,value).compile_sdk==wanted
