"""DT-34: exercise real parser defaults and manifest application, no Gradle."""
import pytest
from devtool import cli


def check(*flags, manifest=None, monkeypatch=None):
    parser=cli.build_parser()
    args=parser.parse_args(['apply','overlay','fixture.zip',*flags])
    cli._normalize_command_aliases(args)
    assert args.command=='apply-overlay'
    if manifest is not None:
        monkeypatch.setattr(cli,'_read_artifact_manifest',lambda *_:manifest)
        cli._apply_artifact_manifest_defaults(args)
    cli._normalize_apply_commit_policy_args(args,parser)
    cli._validate_apply_args(args,parser)
    return args


def test_static_only_accepts_manifest_disabled_builtin_phases(monkeypatch):
    manifest={'schema_version':3,'type':'overlay','validation':{
        'required':True,'restore':False,'build':False,'test':False}}
    args=check('--static-validate',manifest=manifest,monkeypatch=monkeypatch)
    assert args.static_validate and not args.validate
    assert args.skip_restore and args.skip_build and args.skip_test
    assert not cli._exclusive_validation_task_values(args)


def test_skip_without_any_validation_still_fails():
    with pytest.raises(SystemExit):
        check('--skip-restore','--skip-build','--skip-test')


@pytest.mark.parametrize('mode', ['--validate','--validate-only','--defer-validate','--no-validate'])
def test_static_plus_second_mode_is_rejected(mode):
    with pytest.raises(SystemExit):
        check('--static-validate',mode)


def test_normal_validate_and_skips_remain_allowed():
    args=check('--validate','--skip-restore','--skip-build','--skip-test')
    assert args.validate and not args.static_validate


def test_static_only_does_not_allow_exclusive_task():
    with pytest.raises(SystemExit):
        check('--static-validate','--task','assembleDebug')
