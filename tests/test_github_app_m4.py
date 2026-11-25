"""Positive/negative M4 GitHub App credential contracts, no network calls."""
from __future__ import annotations
from datetime import datetime,timezone
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'devtool'/'github_app.py'
spec=importlib.util.spec_from_file_location('github_app',SOURCE)
app=importlib.util.module_from_spec(spec)
sys.modules['github_app']=app
spec.loader.exec_module(app)


def environment(key):
    return {'DEVTOOL_GITHUB_APP_ID':'42','DEVTOOL_GITHUB_APP_INSTALLATION_ID':'300',
            'DEVTOOL_GITHUB_APP_ACCOUNT':'Mikeyphw',
            'DEVTOOL_GITHUB_APP_PRIVATE_KEY_PATH':str(key)}


class HTTP:
    def __init__(self,expires=None,wrong_repo=False,permissions=None):
        self.calls=[]
        self.expiry=expires if expires is not None else int(time.time())+3500
        self.wrong_repo=wrong_repo
        self.permissions=permissions or {'contents':'write','pull_requests':'write',
                                         'actions':'write','checks':'read'}
    def request(self,method,path,credential,payload=None):
        self.calls.append((method,path,credential,payload))
        if path=='/app/installations/300' or path=='/repos/Mikeyphw/devtool/installation':
            return {'id':300,'app_id':42,'account':{'login':'Mikeyphw'},
                    'permissions':self.permissions,'suspended_at':None}
        if path=='/app/installations/300/access_tokens':
            assert method=='POST'
            assert payload['repositories']==['devtool']
            return {'token':'ghs_FAKE_TOKEN_NOT_REAL',
                    'expires_at':datetime.fromtimestamp(self.expiry,timezone.utc).isoformat(),
                    'permissions':payload['permissions'],
                    'repositories':[{'full_name':'Mikeyphw/other' if self.wrong_repo else 'Mikeyphw/devtool','id':777}]}
        if path=='/repos/Mikeyphw/devtool':
            return {'full_name':'Mikeyphw/devtool','id':777}
        raise AssertionError((method,path))


class AppM4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        cls.pem=rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(
            serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())

    def key(self):
        tmp=tempfile.TemporaryDirectory()
        path=Path(tmp.name)/'key.pem'
        path.write_bytes(self.pem)
        path.chmod(0o600)
        self.addCleanup(tmp.cleanup)
        return path

    def provider(self,http=None):
        p=self.key()
        return app.AppInstallationProvider(app.AppConfig.from_environment(environment(p)),http=http or HTTP())

    def test_positive_scoped_token_and_no_plaintext_receipt(self):
        http=HTTP()
        provider=self.provider(http)
        access=provider.issue('Mikeyphw/devtool','branch_write')
        self.assertEqual(access.repository_id,777)
        self.assertEqual(access.capability,'branch_write')
        self.assertEqual(http.calls[2][3]['permissions'],{'contents':'write'})
        self.assertEqual(len(http.calls),4)
        self.assertNotIn('ghs_FAKE_TOKEN',repr(access))
        self.assertNotIn('ghs_FAKE_TOKEN',repr(access.public_receipt()))

    def test_scoped_token_cannot_grant_excess_permission(self):
        for extra in ({'contents':'write'}, {'actions':'write'}, {'metadata':'write'},
                      {'pull_requests':'read'}, {'contents':True}):
            class Excess(HTTP):
                def request(self, method, path, credential, payload=None):
                    result=super().request(method,path,credential,payload)
                    if method=='POST':result['permissions']={**result['permissions'],**extra}
                    return result
            http=Excess()
            with self.subTest(extra=extra), self.assertRaisesRegex(app.GitHubAppError,'REPOSITORY_OR_PERMISSION_UNVERIFIED'):
                self.provider(http).issue('Mikeyphw/devtool','repository_read')
            self.assertEqual(len(http.calls),3)  # Never use the rejected token.

    def test_implicit_metadata_read_is_allowed(self):
        class Metadata(HTTP):
            def request(self, method, path, credential, payload=None):
                result=super().request(method,path,credential,payload)
                if method=='POST':result['permissions']={**result['permissions'],'metadata':'read'}
                return result
        self.assertEqual(self.provider(Metadata()).issue('Mikeyphw/devtool','repository_read').permissions,
                         {'contents':'read','metadata':'read'})

    def test_cached_only_until_safe_refresh_margin(self):
        http=HTTP()
        clock=[int(time.time())]
        provider=self.provider(http)
        provider.clock=lambda:clock[0]
        provider.issue('Mikeyphw/devtool','pr_create')
        provider.issue('Mikeyphw/devtool','pr_create')
        self.assertEqual(len(http.calls),4)
        clock[0]=http.expiry-295
        http.expiry=clock[0]+3500
        provider.issue('Mikeyphw/devtool','pr_create')
        self.assertEqual(len(http.calls),8)

    def test_unknown_capability_and_wrong_account_refused(self):
        p=self.provider()
        with self.assertRaisesRegex(app.GitHubAppError,'APP_CAPABILITY_UNSUPPORTED'):
            p.issue('Mikeyphw/devtool','repo_delete')
        with self.assertRaisesRegex(app.GitHubAppError,'APP_ACCOUNT_REPOSITORY_MISMATCH'):
            p.issue('Other/devtool','branch_write')

    def test_missing_permissions_refused_before_token_post(self):
        http=HTTP(permissions={'contents':'read'})
        p=self.provider(http)
        with self.assertRaisesRegex(app.GitHubAppError,'PERMISSION_MISSING'):
            p.issue('Mikeyphw/devtool','branch_write')
        self.assertTrue(all(method!='POST' for method,*_ in http.calls))

    def test_wrong_repository_in_token_refused(self):
        p=self.provider(HTTP(wrong_repo=True))
        with self.assertRaisesRegex(app.GitHubAppError,'REPOSITORY_OR_PERMISSION_UNVERIFIED'):
            p.issue('Mikeyphw/devtool','pr_read')

    def test_expiring_token_fails_closed(self):
        p=self.provider(HTTP(expires=int(time.time())+100))
        with self.assertRaisesRegex(app.GitHubAppError,'APP_TOKEN_INVALID_OR_EXPIRED'):
            p.issue('Mikeyphw/devtool','branch_write')

    def test_private_key_symlink_and_group_read_refused(self):
        key=self.key()
        key.chmod(0o640)
        with self.assertRaisesRegex(app.GitHubAppError,'PRIVATE_KEY_UNSAFE'):
            app._private_key_bytes(key)
        key.chmod(0o600)
        link=key.parent/'alias.pem'
        link.symlink_to(key)
        with self.assertRaisesRegex(app.GitHubAppError,'PRIVATE_KEY_SYMLINK'):
            app._private_key_bytes(link)

    def test_jwt_iss_and_exp_verified_with_real_public_key(self):
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        import json,base64
        key=self.key()
        now=int(time.time())
        jwt=app._jwt(app.AppConfig.from_environment(environment(key)),now)
        header,payload,sig=jwt.split('.')
        decoded=lambda v:base64.urlsafe_b64decode(v+'='*(-len(v)%4))
        data=json.loads(decoded(payload))
        self.assertEqual((data['iss'],data['iat'],data['exp']),('42',now-60,now+540))
        public=serialization.load_pem_private_key(self.pem,password=None).public_key()
        public.verify(decoded(sig),(header+'.'+payload).encode(),padding.PKCS1v15(),hashes.SHA256())

    def test_explicit_environment_not_accidental_auto_selection(self):
        key=self.key()
        env=environment(key)
        with self.assertRaisesRegex(app.GitHubAppError,'APP_ID_INVALID'):
            app.AppConfig.from_environment({})
        env['DEVTOOL_GITHUB_APP_ID']='bad'
        with self.assertRaisesRegex(app.GitHubAppError,'APP_ID_INVALID'):
            app.AppConfig.from_environment(env)

    def test_http_redirect_and_secret_in_error_not_exposed(self):
        from urllib.error import HTTPError
        class O:
            def open(self,req,timeout):
                raise HTTPError(req.full_url,302,'password-secret',{},None)
        with self.assertRaisesRegex(app.GitHubAppError,'REDIRECT_REFUSED') as caught:
            app._ProviderHTTP(opener=O()).request('GET','/repos/Mikeyphw/devtool','secret')
        self.assertNotIn('secret',str(caught.exception))

    def test_git_requires_https_origin_not_ssh(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            subprocess.run(['git','init','-q'],cwd=root,check=True)
            subprocess.run(['git','remote','add','origin','git@github.com:Mikeyphw/devtool.git'],cwd=root,check=True)
            with self.assertRaisesRegex(app.GitHubAppError,'HTTPS_ORIGIN'):
                app._https_origin(root,'Mikeyphw/devtool')

    def test_git_rejects_unsupported_commands_before_execution(self):
        access=self.provider().issue('Mikeyphw/devtool','branch_write')
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(app.GitHubAppError,'HTTPS_ORIGIN'):
                app.run_git_authenticated(d,['push','origin','main'],access)

    def test_missing_installation_status_blocks_issue(self):
        class S(HTTP):
            def request(self,*args,**kwargs):
                res=super().request(*args,**kwargs)
                if args[1]=='/app/installations/300':res['suspended_at']='2025-01-01T00:00:00Z'
                return res
        with self.assertRaisesRegex(app.GitHubAppError,'IDENTITY_UNVERIFIED'):
            self.provider(S()).issue('Mikeyphw/devtool','checks_read')

    def test_separate_fork_head_installation_config_requires_distinct_identity(self):
        base=self.key()
        head=self.key()
        env=environment(base)
        env.update({'DEVTOOL_GITHUB_APP_HEAD_ID':'42',
                    'DEVTOOL_GITHUB_APP_HEAD_INSTALLATION_ID':'301',
                    'DEVTOOL_GITHUB_APP_HEAD_ACCOUNT':'Other',
                    'DEVTOOL_GITHUB_APP_HEAD_PRIVATE_KEY_PATH':str(head)})
        cfg=app.AppConfig.from_environment(env,role='head')
        self.assertEqual((cfg.installation_id,cfg.account),(301,'Other'))
        with self.assertRaisesRegex(app.GitHubAppError,'APP_ID_INVALID'):
            app.AppConfig.from_environment(environment(base),role='head')

    def test_app_git_transport_never_places_token_in_argv_or_environment(self):
        class Access:
            repository='Mikeyphw/devtool'
            capability='branch_write'
            expires_at=int(time.time())+3600
            token='ghs_SYNTHETIC_SEALED_VALUE'
        captured=[]
        def native_git(argv, **kwargs):
            captured.append((argv,kwargs))
            secret_fd=int(kwargs['env']['DEVTOOL_APP_AUTH_FD'])
            self.assertEqual(os.read(secret_fd,4096),Access.token.encode())
            return subprocess.CompletedProcess(argv,0,'','')
        with (patch.object(app,'_https_origin'),
              patch.object(app.subprocess,'run',side_effect=native_git)):
            result=app.run_git_authenticated('/tmp/worktree',[
                'push','--porcelain','origin','refs/heads/b:refs/heads/b'],Access())
        self.assertEqual(result.returncode,0)
        argv,kwargs=captured[0]
        self.assertNotIn(Access.token,repr(argv))
        self.assertNotIn(Access.token,repr(kwargs['env']))
        self.assertEqual(kwargs['env']['GIT_TERMINAL_PROMPT'],'0')
        self.assertEqual(kwargs['env']['GIT_TRACE'],'0')
        self.assertIn('credential.helper=',argv)
        self.assertTrue(kwargs['pass_fds'])

    def test_permissions_read_does_not_cover_write(self):
        self.assertFalse(app._permission_ok({'contents':'read'},{'contents':'write'}))
        self.assertTrue(app._permission_ok({'contents':'write'},{'contents':'read'}))

if __name__=='__main__': unittest.main()
