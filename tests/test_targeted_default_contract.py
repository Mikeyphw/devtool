"""Validation selection defaults: targeted by default; full always explicit."""
from __future__ import annotations
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from devtool.cli import _apply_implicit_targeted_validation


def cfg(tmp_path: Path, kind: str = "python"):
    return SimpleNamespace(
        repo_root=tmp_path, project_root=tmp_path, kind=kind,
        runner_options={"test_selection": {}, "validation": {}},
    )


def args(**kwargs):
    values = {"command": "validate", "test_profile": "", "validate": False,
              "validate_only": False, "skip_restore": False,
              "skip_build": False, "skip_test": False, "no_validate": False,
              "defer_validate": False, "static_validate": False,
              "affected_base": "", "affected_head": ""}
    values.update(kwargs)
    return Namespace(**values)


def test_implicit_auto_selection_uses_current_affected_path_mapping(tmp_path):
    c=cfg(tmp_path)
    sel={}
    with patch('devtool.affected.git_changed_paths', return_value=('src/login.py','tests/test_login.py')):
        _apply_implicit_targeted_validation(c, args(), sel)
    assert sel['paths']==['src/login.py','tests/test_login.py']
    assert c.runner_options['validation']['selection_origin']=='implicit-affected'


def test_no_affected_path_refuses_instead_of_running_full(tmp_path):
    with patch('devtool.affected.git_changed_paths', return_value=()):
        with pytest.raises(SystemExit, match='TARGETED_VALIDATION_SCOPE_EMPTY'):
            _apply_implicit_targeted_validation(cfg(tmp_path), args(), {})


def test_explicit_test_selection_preserved_without_git_fallback(tmp_path):
    sel={'ids':['tests/test_auth.py::test_login']}
    with patch('devtool.affected.git_changed_paths', side_effect=AssertionError('unexpected fallback')):
        _apply_implicit_targeted_validation(cfg(tmp_path), args(), sel)
    assert sel['ids']==['tests/test_auth.py::test_login']


def test_manifest_exclusive_tasks_not_overridden(tmp_path):
    sel={'tasks':['test']}
    with patch('devtool.affected.git_changed_paths', side_effect=AssertionError('unexpected fallback')):
        _apply_implicit_targeted_validation(cfg(tmp_path), args(), sel)
    assert sel['tasks']==['test']


def test_explicit_full_reserved_profile_does_not_infer_targets(tmp_path):
    c=cfg(tmp_path)
    selection={'profile':'full'}
    with patch('devtool.affected.git_changed_paths', side_effect=AssertionError('unexpected fallback')):
        _apply_implicit_targeted_validation(c, args(test_profile='full'), selection)
    assert selection=={}
    assert c.runner_options['validation']['selection_origin']=='explicit-full'


def test_configured_full_profile_still_owns_its_own_scope(tmp_path):
    c=cfg(tmp_path)
    c.runner_options['test_profiles']={'full': {'paths':['tests/unit']}}
    sel={'profile':'full'}
    _apply_implicit_targeted_validation(c, args(test_profile='full'), sel)
    assert sel=={'profile':'full'}


def test_explicit_test_profile_takes_priority(tmp_path):
    with patch('devtool.affected.git_changed_paths', side_effect=AssertionError('unexpected fallback')):
        _apply_implicit_targeted_validation(cfg(tmp_path), args(), {'profile':'quick'})


def test_artifact_inventory_takes_priority_over_repository_git_diff(tmp_path):
    c=cfg(tmp_path)
    c.runner_options['validation']['changed_paths']=['tests/test_login.py']
    sel={}
    with patch('devtool.affected.git_changed_paths', side_effect=AssertionError('unexpected fallback')):
        _apply_implicit_targeted_validation(c, args(command='apply-overlay',validate=True), sel)
    assert sel['paths']==['tests/test_login.py']


def test_skip_all_builtin_phases_does_not_erase_required_hook(tmp_path):
    with patch('devtool.affected.git_changed_paths', side_effect=AssertionError('unexpected fallback')):
        _apply_implicit_targeted_validation(cfg(tmp_path), args(skip_restore=True,skip_build=True,skip_test=True), {})


def test_static_validation_is_not_convert_to_executable(tmp_path):
    with patch('devtool.affected.git_changed_paths', side_effect=AssertionError('unexpected fallback')):
        _apply_implicit_targeted_validation(cfg(tmp_path), args(static_validate=True), {})


def test_durable_session_control_does_not_invent_new_git_selection(tmp_path):
    with patch('devtool.affected.git_changed_paths', side_effect=AssertionError('must preserve stored plan')):
        _apply_implicit_targeted_validation(cfg(tmp_path), args(_devtool_validation_api_action='start'), {})


def test_overlay_without_requested_validation_has_no_implicit_selection(tmp_path):
    with patch('devtool.affected.git_changed_paths', side_effect=AssertionError('must not run')):
        _apply_implicit_targeted_validation(cfg(tmp_path), args(command='apply-overlay'), {})


def test_validate_all_aggregates_errors_but_stays_targeted(tmp_path):
    selection = {}
    with patch('devtool.affected.git_changed_paths', return_value=('tests/test_scope.py',)):
        _apply_implicit_targeted_validation(cfg(tmp_path), args(command='validate-all'), selection)
    assert selection['paths'] == ['tests/test_scope.py']


def test_validate_all_with_no_affected_scope_refuses_instead_of_full(tmp_path):
    with patch('devtool.affected.git_changed_paths', return_value=()):
        with pytest.raises(SystemExit, match='TARGETED_VALIDATION_SCOPE_EMPTY'):
            _apply_implicit_targeted_validation(cfg(tmp_path), args(command='validate-all'), {})


def test_android_unknown_scope_must_not_fallback_to_full(tmp_path):
    c=cfg(tmp_path,kind='android')
    with patch('devtool.affected.git_changed_paths', return_value=('settings.gradle.kts',)):
        with patch('devtool.android_integration.android_validation_change_scope',return_value='full'):
            with pytest.raises(SystemExit,match='TARGETED_VALIDATION_ANDROID_SCOPE_UNCERTAIN'):
                _apply_implicit_targeted_validation(c,args(),{})


def test_full_profile_menu_command_is_existing_cli_contract():
    from devtool.interactive.validation_wizard import ValidationDraft
    argv=ValidationDraft(method='validate',test_profile='full').command_argv()
    assert '--test-profile' in argv
    assert argv[argv.index('--test-profile')+1]=='full'


def test_manifest_disabled_lifecycle_phases_not_reenabled_by_explicit_validate(monkeypatch):
    from devtool import cli
    manifest={'schema_version':2,'validation':{
        'required':True,'restore':False,'build':False,'test':False,
        'tests':[{'id':'narrow','runner':'python','command':['python3','-V']}],
    }}
    monkeypatch.setattr(cli,'_read_artifact_manifest',lambda _:manifest)
    a=args(command='apply-overlay',artifact='source-bound-fixture.zip',validate=True,
           test_task=[], target='',solution='',commit='',no_commit=True)
    cli._apply_artifact_manifest_defaults(a)
    assert (a.skip_restore,a.skip_build,a.skip_test)==(True,True,True)


def test_manifest_exclusive_tasks_precedence_with_explicit_validation(monkeypatch):
    from devtool import cli
    manifest={'schema_version':2,'validation':{
        'required':True,'restore':False,'build':False,'test':False,'tasks':['test'],
    }}
    monkeypatch.setattr(cli,'_read_artifact_manifest',lambda _:manifest)
    a=args(command='apply-overlay',artifact='source-bound-fixture.zip',validate=True,
           test_task=[], target='',solution='',commit='',no_commit=True)
    cli._apply_artifact_manifest_defaults(a)
    assert a.test_task==['test']
    assert (a.skip_restore,a.skip_build,a.skip_test)==(False,False,False)
