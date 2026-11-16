"""Source-bound native pipeline journal edge preservation; legacy compatibility.

Read-only tests of the existing native public pipeline API. The pipeline
scheduler/transactions are never invoked here.
"""
from __future__ import annotations
import unittest
from devtool.actions_pipeline import _public
from devtool.actions_presentation import project_pipeline


def journal(*, legacy=False):
    return {
        'id':'a'*32, 'name':'test DAG', 'repository':'Mikeyphw/devtool',
        'head_sha':'b'*40, 'manifest_sha256':'c'*64,
        'cancel_requested':False,'node_order':['build','test'],
        'nodes':{
            'build':{'kind':'local-native','state':'success','detail':'',
                     **({} if legacy else {'needs':[]})},
            'test':{'kind':'github-result','state':'pending','detail':'',
                    **({} if legacy else {'needs':['build']})},
        },
    }


class PipelineGraphProjection(unittest.TestCase):
    def test_native_public_receipt_carries_new_dependency_edges(self):
        public=_public(journal())
        self.assertEqual(public['nodes'][0]['needs'],[])
        self.assertEqual(public['nodes'][1]['needs'],['build'])
        self.assertFalse(public['qualified_release'])

    def test_old_durable_journal_remains_compatible_without_fabricated_edges(self):
        public=_public(journal(legacy=True))
        self.assertNotIn('needs',public['nodes'][1])
        projected=project_pipeline(public)
        self.assertNotIn('needs',projected['nodes'][1])

    def test_new_status_projection_retains_order_and_dependencies(self):
        projected=project_pipeline(_public(journal()))
        self.assertEqual([x['id'] for x in projected['nodes']],['build','test'])
        self.assertEqual(projected['nodes'][1]['needs'],['build'])
        self.assertFalse(projected['qualified_release'])
        self.assertFalse(projected['completed_successfully'])
        self.assertFalse(projected['dispatch_enabled'])

    def test_plan_projection_retains_needs_but_refuses_malformed_edges(self):
        plan={'phase':'ACTIONS-06','name':'test','graph':[
            {'id':'build','kind':'local-native','needs':[]},
            {'id':'test','kind':'github-result','needs':['build']}],
            'repository':'Mikeyphw/devtool'}
        self.assertEqual(project_pipeline(plan)['nodes'][1]['needs'],['build'])
        plan['graph'][1]['needs']=['build',None]
        with self.assertRaisesRegex(ValueError,'dependency'):
            project_pipeline(plan)
