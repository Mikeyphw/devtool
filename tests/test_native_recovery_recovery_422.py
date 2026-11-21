"""Source-bound PR 422 recovery regressions: no GitHub network or mutation."""
from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from devtool.recovery import engine as rr

REPO = 'Mikeyphw/MMRL'
ORIGINAL_ID = 1298790213
REPLACEMENT_ID = 1463338730
SHA = 'a' * 40
BASE = 'b' * 40


def pr(number=6, *, merged=True, state='closed'):
    return {'number': number, 'title': f'Original PR {number}', 'body': 'historical',
            'merged': merged, 'merged_at': '2026-11-01T00:00:00Z' if merged else None,
            'state': state, 'draft': False, 'labels': [], 'milestone': None,
            'head': {'ref': f'feature-{number}', 'sha': SHA,
                     'repo': {'full_name': REPO}},
            'base': {'ref': 'master', 'sha': BASE}}


def issue_for(p):
    return {'number': p['number'], 'title': p['title'], 'state': p['state'],
            'body': p['body'], 'labels': [], 'milestone': None, 'pull_request': {}}


class RecreateMergedPRTests(unittest.TestCase):
    def fixture(self, root, prs):
        meta = root / 'primary' / 'metadata'
        meta.mkdir(parents=True)
        rr.write_json(root / 'primary' / 'repo.json',
                      {'id': ORIGINAL_ID, 'default_branch': 'master'})
        rr.write_json(root / 'primary' / 'source-refs.json',
                      {'refs/heads/master': BASE, **{
                          'refs/heads/' + p['head']['ref']: SHA for p in prs}})
        rr.write_json(meta / 'pulls.json', prs)
        rr.write_json(meta / 'issues.json', [issue_for(p) for p in prs])
        return {'stage': 'objects-intent', 'schema': 1, 'session_id': root.name,
                'repo': REPO, 'topology': 'standalone', 'objects': {}}

    def test_all_eight_merged_prs_are_archived_without_post(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = self.fixture(root, [pr(i) for i in (1, 2, 3, 4, 5, 6, 8, 9)])
            with patch.object(rr, 'get_new_repo', return_value={'id': REPLACEMENT_ID}), \
                 patch.object(rr, 'find_import', return_value=None), \
                 patch.object(rr, 'api', side_effect=AssertionError('HTTP write forbidden')), \
                 patch.object(rr, 'live_refs', side_effect=AssertionError('merged refs need no lookup')):
                rr.restore_objects(root, state)
            self.assertEqual(state['stage'], 'objects-done')
            self.assertEqual(len(state['objects']), 8)
            for n in (1, 2, 3, 4, 5, 6, 8, 9):
                self.assertEqual(state['objects'][f'pr:{n}']['reason'],
                                 'merged-history-cannot-be-recreated')
            self.assertEqual(rr.load_state(root)['objects'], state['objects'])

    def test_existing_archival_decision_is_unchanged_on_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = self.fixture(root, [pr()])
            state['objects']['pr:6'] = {
                'archived_only': True, 'reason': 'missing/external/colliding original head'}
            with patch.object(rr, 'get_new_repo', return_value={'id': REPLACEMENT_ID}), \
                 patch.object(rr, 'find_import', return_value=None), \
                 patch.object(rr, 'api', side_effect=AssertionError('no API POST')):
                rr.restore_objects(root, state)
            self.assertEqual(state['objects']['pr:6']['reason'],
                             'missing/external/colliding original head')

    def test_archived_marker_conflict_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = self.fixture(root, [pr()])
            state['objects']['pr:6'] = {
                'archived_only': True, 'reason': 'merged-history-cannot-be-recreated'}
            with patch.object(rr, 'get_new_repo', return_value={'id': REPLACEMENT_ID}), \
                 patch.object(rr, 'find_import', return_value={'number': 14, 'title': 'Original PR 6'}), \
                 patch.object(rr, 'api', side_effect=AssertionError('no API POST')):
                with self.assertRaisesRegex(rr.Refused, 'archived-only journal conflicts'):
                    rr.restore_objects(root, state)
            self.assertEqual(state['stage'], 'objects-intent')

    def test_preexisting_live_import_cannot_be_silently_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = self.fixture(root, [pr()])
            state['objects']['pr:6'] = {'number': 16}
            with patch.object(rr, 'get_new_repo', return_value={'id': REPLACEMENT_ID}), \
                 patch.object(rr, 'find_import', return_value={'number': 17, 'title': 'Original PR 6'}), \
                 patch.object(rr, 'api', side_effect=AssertionError('no API POST')):
                with self.assertRaisesRegex(rr.Refused, 'journaled imported object identity changed'):
                    rr.restore_objects(root, state)
            self.assertEqual(state['objects']['pr:6'], {'number': 16})

    def test_unmerged_422_remains_refusal_with_original_pr_number(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = self.fixture(root, [pr(7, merged=False, state='open')])
            refs = {'refs/heads/master': BASE, 'refs/heads/feature-7': SHA}
            calls = []
            def reject(endpoint, *, method='GET', payload=None, **kwargs):
                calls.append((method, endpoint))
                raise rr.Refused('GitHub API POST refused: Validation Failed (HTTP 422)')
            with patch.object(rr, 'get_new_repo', return_value={'id': REPLACEMENT_ID}), \
                 patch.object(rr, 'find_import', return_value=None), \
                 patch.object(rr, 'live_refs', return_value=refs), \
                 patch.object(rr, 'api', side_effect=reject):
                with self.assertRaisesRegex(rr.Refused, r'original unmerged PR #7 creation refused'):
                    rr.restore_objects(root, state)
            self.assertEqual(calls, [('POST', f'repos/{REPO}/pulls')])
            self.assertEqual(state['stage'], 'objects-intent')
            self.assertEqual(state['objects'], {})

    def test_unmerged_live_pr_is_still_importable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = self.fixture(root, [pr(7, merged=False, state='open')])
            refs = {'refs/heads/master': BASE, 'refs/heads/feature-7': SHA}
            post_calls = []
            def api(endpoint, *, method='GET', payload=None, **kwargs):
                if method == 'POST':
                    post_calls.append(payload)
                    return {'number': 19}
                if endpoint.endswith('/issues/19'):
                    return {'number': 19, 'state': 'open', 'title': 'Original PR 7',
                            'labels': [], 'milestone': None}
                if endpoint.endswith('/pulls/19'):
                    return {'number': 19, 'state': 'open'}
                raise AssertionError('unexpected endpoint ' + endpoint)
            with patch.object(rr, 'get_new_repo', return_value={'id': REPLACEMENT_ID}), \
                 patch.object(rr, 'find_import', return_value=None), \
                 patch.object(rr, 'live_refs', return_value=refs), \
                 patch.object(rr, 'api', side_effect=api), \
                 patch.object(rr, 'verify_imported_issue_details'), \
                 patch.object(rr, 'verify_import'):
                rr.restore_objects(root, state)
            self.assertEqual(state['objects']['pr:7'], {'number': 19})
            self.assertEqual(state['stage'], 'objects-done')
            self.assertEqual(len(post_calls), 1)
            self.assertEqual(post_calls[0]['base'], 'master')
            self.assertEqual(post_calls[0]['head'], 'feature-7')
            self.assertIn('Archived from the original repository', post_calls[0]['body'])


if __name__ == '__main__':
    unittest.main()
