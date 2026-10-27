"""R03: AOSP absence exit-code classification; no trust in shell UID output."""
from __future__ import annotations
from types import SimpleNamespace
import pytest
from devtool.android_package_presence import classify_pm_presence, query_package_presence
from devtool.github_actions import GitHubActionsError

PACKAGE='org.example.app'

def check(path_code=1,path_out='',path_err='',list_code=0,list_out='',list_err=''):
    return classify_pm_presence(PACKAGE,path_code,path_out,path_err,list_code,list_out,list_err)

def test_confirmed_absence_not_pm_error():
    got=check()
    assert got['presence']=='absent' and got['trusted_broker_identity'] is False

def test_existing_package_paths_require_exact_consistency():
    result=check(0,'package:/data/app/~~abc/org.example.app/base.apk\n', '',0,'package:'+PACKAGE+'\n','')
    assert result['presence']=='present' and result['path_count']==1

@pytest.mark.parametrize('kwargs',[
    {'path_code':1,'path_err':'SecurityException'},
    {'path_code':1,'list_out':'package:'+PACKAGE+'\n'},
    {'path_code':0},
    {'path_code':0,'path_out':'package:/data/app/org.example.app/base.apk\n'},
    {'path_code':0,'path_out':'package:/data/app/org.example.app/base.apk\n','list_out':'package:org.example.app.fake\n'},
    {'path_code':0,'path_out':'package:/data/app/org.example.app/base.apk\n','list_out':'package:'+PACKAGE+'\n','list_err':'Exception'},
    {'path_code':0,'path_out':'package:/data/app/org.example.app/base.apk\n','list_out':'package:'+PACKAGE+'\n','list_code':1},
    {'path_code':0,'path_out':'package:/data/app/org.example.app/base.apk\n','list_out':'package:'+PACKAGE+'\npackage:'+PACKAGE+'\n'},
    {'path_code':0,'path_out':'package:../../unsafe.apk\n','list_out':'package:'+PACKAGE+'\n'},
])
def test_ambiguous_absence_or_presence_fails_closed(kwargs):
    with pytest.raises(GitHubActionsError):check(**kwargs)

def test_same_transport_same_user_and_no_mutation(monkeypatch):
    monkeypatch.setattr('devtool.android_package_presence.shutil.which',lambda _: '/usr/bin/adb')
    calls=[]
    def run(argv,**kw):
        calls.append(argv)
        if 'path' in argv:return SimpleNamespace(returncode=1,stdout='',stderr='')
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr('devtool.android_package_presence.subprocess.run',run)
    got=query_package_presence('SERIAL-01',PACKAGE,android_user=10)
    assert got['presence']=='absent' and got['android_user']==10 and not got['device_qualified']
    assert len(calls)==2
    assert all(cmd[:5]==['/usr/bin/adb','-s','SERIAL-01','shell','pm'] for cmd in calls)
    assert all(cmd.count('--user')==1 and cmd[cmd.index('--user')+1]=='10' for cmd in calls)
    assert not any(any(x in cmd for x in ('install','uninstall','clear','su')) for cmd in calls)

@pytest.mark.parametrize('serial,user',[('',0),('A B',0),('serial',-1),('serial',True),('serial',10000)])
def test_invalid_device_boundary_rejected_without_call(monkeypatch,serial,user):
    monkeypatch.setattr('devtool.android_package_presence.subprocess.run',lambda *a,**kw:pytest.fail('No execution allowed'))
    with pytest.raises(GitHubActionsError):query_package_presence(serial,PACKAGE,android_user=user)
