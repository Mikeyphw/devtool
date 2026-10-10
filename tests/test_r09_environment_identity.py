from __future__ import annotations
from pathlib import Path
from devtool.android.environment import EnvironmentView
from devtool.models import ProjectConfig

def config(tmp_path):
    return ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind='android', runner='android')

def test_secret_value_rotation_does_not_change_identity(tmp_path):
    cfg=config(tmp_path)
    a=EnvironmentView.from_config(cfg,env={'PATH':'/usr/bin','API_TOKEN':'alpha-private','SIGNING_PASSWORD':'one'})
    b=EnvironmentView.from_config(cfg,env={'PATH':'/usr/bin','API_TOKEN':'beta-private','SIGNING_PASSWORD':'two'})
    assert a.identity == b.identity
    assert a.values != b.values
    assert 'alpha-private' not in str(a.as_dict())

def test_semantic_sdk_and_path_still_change_identity(tmp_path):
    cfg=config(tmp_path)
    a=EnvironmentView.from_config(cfg,env={'ANDROID_HOME':'/sdk/a','PATH':'/bin'})
    b=EnvironmentView.from_config(cfg,env={'ANDROID_HOME':'/sdk/b','PATH':'/bin'})
    assert a.identity != b.identity

def test_source_and_credential_key_presence_are_distinct(tmp_path):
    cfg=config(tmp_path)
    a=EnvironmentView.from_config(cfg,env={'PATH':'/bin'})
    b=EnvironmentView.from_config(cfg,env={'PATH':'/bin','ACCESS_KEY_ID':'any'})
    assert a.identity != b.identity

def test_private_key_and_credentials_are_excluded(tmp_path):
    cfg=config(tmp_path)
    a=EnvironmentView.from_config(cfg,env={'PRIVATE_KEY':'one','AWS_SECRET_ACCESS_KEY':'two'})
    b=EnvironmentView.from_config(cfg,env={'PRIVATE_KEY':'three','AWS_SECRET_ACCESS_KEY':'four'})
    assert a.identity == b.identity

def test_config_env_override_secret_is_excluded(tmp_path):
    cfg=config(tmp_path)
    cfg.env['API_TOKEN']='first'
    a=EnvironmentView.from_config(cfg,env={'PATH':'/bin'})
    cfg.env['API_TOKEN']='second'
    b=EnvironmentView.from_config(cfg,env={'PATH':'/bin'})
    assert a.identity == b.identity

def test_non_secret_build_setting_keeps_identity_precision(tmp_path):
    cfg=config(tmp_path)
    a=EnvironmentView.from_config(cfg,env={'BUILD_FLAVOR':'a'})
    b=EnvironmentView.from_config(cfg,env={'BUILD_FLAVOR':'b'})
    assert a.identity != b.identity
