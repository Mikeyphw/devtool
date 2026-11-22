"""M8 native authority integration and M6 Rich live event bridge contracts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from devtool import m8_automation as m8, m8_webhooks as wh

SHA='a'*40


class M8NativeBridgeTests(unittest.TestCase):
    def test_native_mutation_identities_are_registered(self):
        try:from devtool.delivery_session import _OPERATIONS
        except ModuleNotFoundError:self.skipTest('Standalone package, checked after native overlay apply')
        for action in ('pr_comment','pr_labels','commit_status'):
            self.assertIn(action,_OPERATIONS)

    def test_github_app_capabilities_have_restricted_scopes(self):
        try:from devtool.github_app import _PERMISSIONS
        except ModuleNotFoundError:self.skipTest('Standalone package, checked after native overlay apply')
        self.assertEqual(_PERMISSIONS['pr_maintain'],{'pull_requests':'write','contents':'read'})
        self.assertEqual(_PERMISSIONS['status_write'],{'statuses':'write','contents':'read'})

    def test_m6_bridge_is_local_redacted_and_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            root=folder/'devtool/m8-webhooks'
            root.mkdir(parents=True,mode=0o700)
            (root/'automation-policy.json').write_text(json.dumps({'policy_id':'test','head_sha':SHA,
                'expires_at':'2030-11-22T01:30:00Z','secret':'DO_NOT_SHOW_THIS'}))
            os.chmod(root/'automation-policy.json',0o600)
            processed=root/'processed';processed.mkdir(mode=0o700)
            ev={'delivery':'11111111-1111-4111-8111-111111111111',
                'state':'OBSERVED','secret':'SENSITIVE_EVENT_PAYLOAD'}
            (processed/'one.json').write_text(json.dumps(ev));os.chmod(processed/'one.json',0o600)
            before={str(p):p.stat().st_mtime_ns for p in root.rglob('*') if p.is_file()}
            with patch.dict(os.environ,{'XDG_STATE_HOME':str(folder)}),\
                 patch.object(m8.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=SHA+'\n')):
                events=m8.m6_events(folder,limit=20)
            after={str(p):p.stat().st_mtime_ns for p in root.rglob('*') if p.is_file()}
            self.assertEqual(before,after)
            self.assertEqual(len(events),1)
            self.assertEqual(events[0]['domain'],'m8')
            self.assertNotIn('DO_NOT_SHOW_THIS',str(events))
            self.assertNotIn('SENSITIVE_EVENT_PAYLOAD',str(events))

    def test_m6_bridge_rejects_wrong_source(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td);root=folder/'devtool/m8-webhooks';root.mkdir(parents=True,mode=0o700)
            (root/'automation-policy.json').write_text(json.dumps({'head_sha':SHA,
                'expires_at':'2030-11-22T01:30:00Z','policy_id':'dummy'}))
            os.chmod(root/'automation-policy.json',0o600)
            with patch.dict(os.environ,{'XDG_STATE_HOME':str(folder)}),\
                 patch.object(m8.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='b'*40+'\n')):
                self.assertEqual(m8.m6_events(folder),[])

    def test_m6_event_filter_includes_m8(self):
        try:from devtool import operator_event_feed as feed
        except (ImportError,ModuleNotFoundError):self.skipTest('M6 bridge installed through native overlay')
        self.assertIn('m8',feed.DOMAIN_FILTERS)
        events={'events':[{'domain':'m8','event_id':'e','kind':'M8 observation',
                           'status':'completed','detail':{},'timestamp':None}]}
        rows=feed.visible_events(events,domain='m8')
        self.assertEqual(len(rows),1)
        self.assertEqual(feed.event_key(rows[0]),'m8:e')

if __name__=='__main__':unittest.main()
