"""Native alerts: user-global policy, app probe, independent channel boundaries."""
from __future__ import annotations

import os
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from devtool import apply_notifications
from devtool.apply_notifications import ApplyNotification
from devtool.notification_controls import companion_installed, os_output_enabled, output_mode


@pytest.mark.parametrize('value,expected', [('0','off'),('no','off'),('false','off'),('disabled','off'),('1','on'),('yes','on'),('auto','auto'),('INVALID','off')])
def test_environment_mode(value, expected, monkeypatch, tmp_path):
    monkeypatch.setenv('DEVTOOL_GLOBAL_CONFIG', str(tmp_path/'absent.toml'))
    monkeypatch.setenv('DEVTOOL_NOTIFICATIONS', value)
    assert output_mode('notifications') == expected


def test_global_independent_flags_and_env_precedence(monkeypatch, tmp_path):
    cfg = tmp_path/'config.toml'
    cfg.write_text('[cli.notifications]\nnative_notifications="off"\nnative_toasts="on"\n')
    monkeypatch.setenv('DEVTOOL_GLOBAL_CONFIG',str(cfg))
    monkeypatch.delenv('DEVTOOL_NOTIFICATIONS',raising=False)
    monkeypatch.delenv('DEVTOOL_TOASTS',raising=False)
    assert output_mode('notifications') == 'off'
    assert output_mode('toasts') == 'on'
    monkeypatch.setenv('DEVTOOL_NOTIFICATIONS','on')
    assert output_mode('notifications') == 'on'
    assert output_mode('toasts') == 'on'


def test_global_config_template_and_doctor(monkeypatch,tmp_path):
    from devtool.config import global_config_doctor_entries, minimal_global_config_template, load_global_config
    text = minimal_global_config_template()
    assert '[cli.notifications]' in text
    cfg = tmp_path/'config.toml'
    cfg.write_text('[cli.notifications]\nnative_notifications="off"\nnative_toasts="auto"\n')
    assert not [x for x in global_config_doctor_entries(load_global_config(cfg)[0]) if 'notification' in x[0]]
    cfg.write_text('[cli.notifications]\nnative_toasts="banana"\n')
    assert any('cli.notifications.native_toasts' in x[0] for x in global_config_doctor_entries(load_global_config(cfg)[0]))


def test_companion_probed_unprivileged_with_positive_package_evidence():
    with patch('devtool.android_live_hud.AndroidLiveHudBridge._package_commands', side_effect=lambda package: [['pm','path',package]]), \
         patch('devtool.notification_controls.subprocess.run', side_effect=lambda argv,**kw: CompletedProcess(argv,0,'package:/data/app/base.apk' if argv[-1].endswith('.debug') else '')) as runner:
        assert companion_installed() is True
        assert runner.called
        assert all('su' not in args[0][0] for args in runner.call_args_list)


def test_companion_not_assumed_present_without_pm_confirmation():
    with patch('devtool.android_live_hud.AndroidLiveHudBridge._package_commands', return_value=[['pm','path','x']]), \
         patch('devtool.notification_controls.subprocess.run',return_value=CompletedProcess([],0,'')):
        assert companion_installed() is False


@pytest.mark.parametrize('notif,toast,detected,expected',[
    ('auto','auto',True,(False,False)),
    ('auto','auto',False,(True,True)),
    ('on','auto',True,(True,False)),
    ('off','on',False,(False,True)),
    ('off','off',True,(False,False)),
])
def test_termux_independent_outputs(monkeypatch,tmp_path,notif,toast,detected,expected):
    cfg=tmp_path/'empty.toml';monkeypatch.setenv('DEVTOOL_GLOBAL_CONFIG',str(cfg))
    monkeypatch.setenv('DEVTOOL_NOTIFICATIONS',notif)
    monkeypatch.setenv('DEVTOOL_TOASTS',toast)
    note=ApplyNotification('Title','Body','Toast')
    with patch('devtool.apply_notifications._prefer_android_side',return_value=True), \
         patch('devtool.apply_notifications._android_app_run_notification_active',return_value=False), \
         patch('devtool.notification_controls.companion_installed',return_value=detected), \
         patch('devtool.apply_notifications._post_termux_notification',return_value=True) as native, \
         patch('devtool.apply_notifications._post_termux_toast',return_value=True) as toast_call:
        apply_notifications.post_apply_notification(note)
        assert (native.call_count==1, toast_call.call_count==1)==expected


def test_desktop_system_notifications_muted(monkeypatch,tmp_path):
    monkeypatch.setenv('DEVTOOL_GLOBAL_CONFIG',str(tmp_path/'none.toml'))
    monkeypatch.setenv('DEVTOOL_NOTIFICATIONS','off')
    with patch('devtool.apply_notifications._prefer_android_side',return_value=False), \
         patch('devtool.apply_notifications._post_desktop') as desktop:
        apply_notifications.post_apply_notification(ApplyNotification('T','B','Toast'))
        desktop.assert_not_called()


def test_legacy_apply_level_disable_still_short_circuits(monkeypatch):
    monkeypatch.setenv('DEVTOOL_APPLY_NOTIFICATION','off')
    assert apply_notifications.apply_notifications_enabled(object()) is False
