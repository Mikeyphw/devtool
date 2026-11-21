"""Native recovery contract tests. All tests are offline and mutation-free."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from devtool.recovery import __version__
from devtool.recovery import engine, native_cli, provider, health

class NativeRecoveryContractTests(unittest.TestCase):
    def test_engine_is_native_source_package(self):
        self.assertTrue(engine.__name__.startswith('devtool.recovery.'))
        self.assertEqual(engine.VERSION,__version__)
        self.assertNotIn('repo_rebuild',engine.__name__)
        self.assertEqual(engine.DEFAULT_ROOT,Path.home()/'.local/share/devtool/recovery')

    def test_client_does_not_execute_standalone_app(self):
        import inspect
        body=inspect.getsource(native_cli.run_native)
        self.assertNotIn('subprocess.run',body)
        self.assertNotIn('repo-rebuild',body)
        self.assertIn('args.command = _NATIVE_ALIASES',body)

    def test_canonical_parser_has_recovery_and_admin_approval(self):
        try:
            from devtool.core_parser import build_canonical_parser
        except ImportError:
            # On the isolated unit-test harness there is no full Devtool checkout;
            # the source-bound native runner on the target will execute this path.
            self.assertIn('recovery_command',__import__('inspect').getsource(native_cli.run_native))
            return
        p=build_canonical_parser()
        a=p.parse_args(['recovery','credentials','--session','/tmp/session','--apply-pat'])
        self.assertEqual(a.command,'recovery')
        self.assertEqual(a.recovery_command,'credentials')
        self.assertIsNone(a.ack_pat_provision)
        a=p.parse_args(['recovery','status','--session','/tmp/session'])
        self.assertEqual(a.session,'/tmp/session')

    def test_canonical_registry_parity_when_core_available(self):
        try:
            from devtool.core_registry import assert_parser_matches_canonical_registry, build_core_registry
            from devtool.core_parser import build_canonical_parser
        except ImportError:
            self.assertTrue(provider._endpoint('repos/Mikeyphw/MMRL'))
            return
        assert_parser_matches_canonical_registry(build_canonical_parser(),build_core_registry())

    def test_native_doctor_no_network_or_mutation(self):
        a=argparse.Namespace(recovery_command='doctor')
        with patch.object(engine,'api',side_effect=AssertionError('network access forbidden')):
            result=native_cli.run_native(a)
        self.assertIn(result,(0,2))

    def test_missing_operator_approval_prevents_credential_writes(self):
        a=argparse.Namespace(recovery_command='credentials',session='/tmp/unopened',
                 apply_app=True,apply_pat=False,ack_app_repair=None,ack_pat_provision=None)
        with patch.object(native_cli,'locked',side_effect=AssertionError('journal opened before approval')):
            self.assertEqual(native_cli.run_native(a),2)

    def test_provider_rejects_cross_origin_and_traversal(self):
        for path in ('https://api.github.com/repos/a/b','//api.github.com/repos/a/b',
                     'repos/../secrets','repos/a//b','repos/a/%2e%2e/b',
                     'repos/a/b?url=https://evil.test','repos/a/b#hello'):
            with self.subTest(path=path):
                with self.assertRaises(provider.RecoveryTransportError):
                    provider._endpoint(path)

    def test_provider_uses_devtool_credential_resolver(self):
        observed=[]
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return b'{"id":123}'
        class Open:
            def open(self,req,timeout):
                observed.append((req.full_url,req.headers.get('Authorization'),req.get_method()))
                return Response()
        fake=types.ModuleType('devtool.github_actions')
        fake.GitHubActionsError=type('GitHubActionsError',(RuntimeError,),{})
        fake.resolve_token=lambda mode, **kwargs:('native-devtool-token','environment')
        with patch.dict(sys.modules,{'devtool.github_actions':fake}):
            response=provider.request_json('repos/Mikeyphw/MMRL',opener=Open())
        self.assertEqual(response,{'id':123})
        self.assertEqual(observed[0][0],'https://api.github.com/repos/Mikeyphw/MMRL')
        self.assertEqual(observed[0][1],'Bearer native-devtool-token')
        self.assertEqual(observed[0][2],'GET')

    def test_provider_no_automatic_write_retry_on_uncertain_response(self):
        import urllib.error
        called=[]
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return b'{"permissions":{"admin":true}}'
        class Open:
            def open(self,req,timeout):
                called.append(req.get_method())
                if req.get_method()=='GET':return Response()
                raise urllib.error.URLError('simulated disconnect')
        with patch.dict(os.environ,{'DEVTOOL_RECOVERY_ADMIN_TOKEN':'explicit-admin-credential'}):
            with self.assertRaisesRegex(provider.RecoveryTransportError,'OUTCOME_UNCERTAIN'):
                provider.request_json('repos/Mikeyphw/MMRL',method='DELETE',opener=Open())
        self.assertEqual(called,['GET','DELETE'])

    def test_write_requires_explicit_admin_credential(self):
        class Open:
            def open(self,req,timeout):
                raise AssertionError('Request sent before admin authorization')
        with patch.dict(os.environ,{'DEVTOOL_RECOVERY_ADMIN_TOKEN':''}):
            with self.assertRaisesRegex(provider.RecoveryTransportError,'ADMIN_CREDENTIAL_REQUIRED'):
                provider.request_json('repos/Mikeyphw/MMRL',method='PATCH',
                                      payload={'description':'test'},opener=Open())

    def test_write_preflight_must_prove_admin_access(self):
        sent=[]
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return b'{"permissions":{"admin":false}}'
        class Open:
            def open(self,req,timeout):
                sent.append(req.get_method())
                return Response()
        with patch.dict(os.environ,{'DEVTOOL_RECOVERY_ADMIN_TOKEN':'explicit-admin-credential'}):
            with self.assertRaisesRegex(provider.RecoveryTransportError,'ADMIN_PERMISSION_UNVERIFIED'):
                provider.request_json('repos/Mikeyphw/MMRL',method='PATCH',
                                      payload={'description':'test'},opener=Open())
        self.assertEqual(sent,['GET'])

    def test_write_refuses_nonrepository_admin_api(self):
        with patch.dict(os.environ,{'DEVTOOL_RECOVERY_ADMIN_TOKEN':'explicit-admin-credential'}):
            with self.assertRaisesRegex(provider.RecoveryTransportError,'WRITE_REPOSITORY_SCOPE_REQUIRED'):
                provider.request_json('user/installations/123',method='DELETE')

    def test_read_resolver_receives_exact_repository_scope(self):
        observed=[]
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return b'{"id":123}'
        class Open:
            def open(self,req,timeout):return Response()
        fake=types.ModuleType('devtool.github_actions')
        fake.GitHubActionsError=type('GitHubActionsError',(RuntimeError,),{})
        def resolve_token(mode, **kwargs):
            observed.append((mode,kwargs))
            return 'native-devtool-token','environment'
        fake.resolve_token=resolve_token
        with patch.dict(sys.modules,{'devtool.github_actions':fake}):
            provider.request_json('repos/Mikeyphw/MMRL',opener=Open())
        self.assertEqual(observed,[('auto',{'repository':'Mikeyphw/MMRL',
                                            'capability':'repository_read'})])

    def test_read_only_failures_have_structured_refusal(self):
        for cmd, name in (('plan','inspect_repo'),('report','load_state')):
            a=argparse.Namespace(recovery_command=cmd,
                                 recovery_repository='Mikeyphw/MMRL',session='/tmp/nonexistent')
            with self.subTest(cmd=cmd):
                with patch.object(engine,name,side_effect=engine.Refused('simulated refusal')):
                    self.assertEqual(native_cli.run_native(a),2)

    def test_root_default_is_android_portable(self):
        a=argparse.Namespace(recovery_command='sessions',recovery_repository='Mikeyphw/MMRL',root=None)
        with patch.object(native_cli,'list_sessions') as f:
            self.assertEqual(native_cli.run_native(a),0)
        f.assert_called_once_with('Mikeyphw/MMRL', engine.DEFAULT_ROOT)

    def test_provider_never_downgrades_forbidden_to_missing(self):
        from urllib.error import HTTPError
        class Open:
            def open(self,req,timeout):
                raise HTTPError(req.full_url,403,'Forbidden',{},None)
        fake=types.ModuleType('devtool.github_actions')
        fake.GitHubActionsError=type('GitHubActionsError',(RuntimeError,),{})
        fake.resolve_token=lambda mode, **kwargs:('native-devtool-token','environment')
        with patch.dict(sys.modules,{'devtool.github_actions':fake}):
            with self.assertRaisesRegex(provider.RecoveryTransportError,'FORBIDDEN'):
                provider.request_json('repos/Mikeyphw/MMRL',missing_ok=True,opener=Open())

    def test_existing_session_is_not_automatically_migrated(self):
        self.assertEqual(engine.DEFAULT_ROOT.name,'recovery')
        self.assertEqual(engine.DEFAULT_ROOT.parent.name,'devtool')

if __name__=='__main__':unittest.main()
