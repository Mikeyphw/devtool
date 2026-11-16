"""UX-RICH01 completion: verified, opt-in, bounded remote log inspection."""
from __future__ import annotations
import hashlib
from io import StringIO
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from rich.console import Console
from devtool.delivery_remote_logs import (
    verified_remote_run, sanitized_archive_lines, fetch_selected_run_logs,
    GitHubActionsError,
)
from devtool.delivery_rich import NativeRichMonitor, ci_snapshot_view
from devtool.summary import render_artifact_manifest


class Client:
    def __init__(self):
        self.item={'id':42,'run_attempt':2,'head_sha':'a'*40,
                   'event':'pull_request','workflow_id':33,
                   'repository':{'full_name':'Mikeyphw/devtool'}}
    def get_run(self, slug, rid):
        assert slug=='Mikeyphw/devtool'
        return dict(self.item)


ROW={'id':42,'attempt':2,'head_sha':'a'*40,'event':'pull_request','workflow_id':33}


def log_archive(root:Path, files:dict[str,str]):
    path=root/'runs.zip'
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,value in files.items():z.writestr(name,value)
    return path,hashlib.sha256(path.read_bytes()).hexdigest()


class RemoteLogTests(unittest.TestCase):
    def test_exact_run_source_and_attempt(self):
        self.assertEqual(verified_remote_run(Client(),'Mikeyphw/devtool',ROW),42)
        for key,value in [('attempt',1),('head_sha','b'*40),('workflow_id',32),('event','push'),('id',43)]:
            changed=dict(ROW,**{key:value})
            with self.subTest(key=key),self.assertRaises(GitHubActionsError):
                verified_remote_run(Client(),'Mikeyphw/devtool',changed)

    def test_mismatched_repo_refused(self):
        client=Client();client.item['repository']['full_name']='other/devtool'
        with self.assertRaises(GitHubActionsError):
            verified_remote_run(client,'Mikeyphw/devtool',ROW)

    def test_redacted_bounded_archive(self):
        with tempfile.TemporaryDirectory() as t:
            path,sha=log_archive(Path(t),{'job/step.txt':'hello\nAuthorization: Bearer SECRET-KEY-123\nerror: bad result\n'})
            rows=sanitized_archive_lines(path,sha)
            self.assertTrue(any('hello' in s for s in rows))
            self.assertTrue(any('error: bad result' in s for s in rows))
            self.assertFalse(any('SECRET-KEY-123' in s for s in rows))

    def test_multiline_private_key_never_displayed(self):
        with tempfile.TemporaryDirectory() as t:
            key=('-----BEGIN PRIVATE KEY-----\n'
                 'AAAAAAAAAAAAAAAAAAAAAAA\n'
                 'BBBBBBBBBBBBBBBBBBBBBBB\n'
                 '-----END PRIVATE KEY-----\n')
            path,sha=log_archive(Path(t),{'private.txt':key+'after\n'})
            lines=sanitized_archive_lines(path,sha)
            self.assertFalse(any('AAAA' in v or 'BBBB' in v for v in lines))
            self.assertTrue(any('after' in v for v in lines))

    def test_archive_sha_and_symlink_refused(self):
        with tempfile.TemporaryDirectory() as t:
            path,sha=log_archive(Path(t),{'logs.txt':'safe'})
            with self.assertRaises(GitHubActionsError):
                sanitized_archive_lines(path,'b'*64)
            link=Path(t)/'link.zip';link.symlink_to(path)
            with self.assertRaises(GitHubActionsError):
                sanitized_archive_lines(link,sha)

    def test_traversal_zip_and_size_guard(self):
        with tempfile.TemporaryDirectory() as t:
            path,sha=log_archive(Path(t),{'../secret.txt':'unsafe'})
            with self.assertRaises(GitHubActionsError):
                sanitized_archive_lines(path,sha)
            path,sha=log_archive(Path(t),{'steps.txt':'a'*(25*1024*1024)})
            with self.assertRaises(GitHubActionsError):
                sanitized_archive_lines(path,sha)

    def test_native_log_authority_called_once(self):
        with tempfile.TemporaryDirectory() as t:
            path,sha=log_archive(Path(t),{'ok.txt':'hello'})
            with patch('devtool.delivery_remote_logs.remote_logs',return_value={
                'repository':'Mikeyphw/devtool','run_id':42,'sha256':sha,'archive':str(path)}) as dl:
                out=fetch_selected_run_logs(Client(),'Mikeyphw/devtool',ROW)
                self.assertIn('hello',out)
                dl.assert_called_once()

    def test_monitor_no_background_download_and_explicit_log_selection(self):
        called=[]
        def getter(row):
            called.append(row['id']);return ('remote entry one','remote entry two')
        ui=NativeRichMonitor(kind='ci',enabled=True,
             console=Console(file=StringIO(),width=60,force_terminal=False),
             log_fetcher=getter)
        ui.last={'state':'CHECKS_RUNNING','runs':[dict(ROW,name='job',status='in_progress')]}
        self.assertFalse(called)
        ui.key('l')
        self.assertFalse(called)  # Keyboard thread MUST NOT call provider
        ui._consume_log_fetch()  # Actual foreground poll thread
        self.assertEqual(called,[42])
        self.assertEqual(ui.log_lines,('remote entry one','remote entry two'))
        ui.key('l')
        self.assertFalse(ui.log_requested)
        ui.key('q')
        self.assertTrue(ui.detached)

    def test_native_apply_manifest_compact_opt_in(self):
        sample={'name':'test','type':'overlay','repository':{'name':'Mikeyphw/devtool'},
                'inventory':{'entries':[{'action':'modify','path':'README.md'}]},
                'apply':{'atomic':True,'conflict_policy':'fail'}}
        out=StringIO();c=Console(file=out,width=120,force_terminal=False)
        render_artifact_manifest(sample,console_obj=c,compact=True)
        compact=out.getvalue()
        self.assertIn('Artifact review',compact)
        self.assertNotIn('Execution contract',compact)
        self.assertNotIn('Repository layout',compact)
        out=StringIO();c=Console(file=out,width=120,force_terminal=False)
        render_artifact_manifest(sample,console_obj=c,compact=False)
        self.assertIn('Artifact summary',out.getvalue())


if __name__=='__main__':unittest.main()
