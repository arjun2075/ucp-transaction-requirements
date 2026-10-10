import unittest
import json
from copy import deepcopy
from pathlib import Path
from jsonschema import ValidationError, Draft202012Validator
from integration import *

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.provider=Provider('fixture-provider')
        self.business=Business(profile(),self.provider)
    def resolve(self):
        for kind in self.business.required:
            self.assertTrue(self.business.submit(kind,self.provider.issue(self.business.config(kind))))
    def test_declarations_valid_against_upstream(self):
        for kind in ['platform','business','response']:
            validate_declarations(profile(),kind)
    def test_exact_version_mismatch_preserves_required_block(self):
        p=profile(); p[S][0]['version']='2026-10-08'
        b=Business(p,self.provider)
        self.assertNotIn(S,b.active)
        self.assertEqual(b.complete()['status'],'incomplete')
        self.assertIn('required_workflow',b.snapshot()['messages'][0]['code'])
    def test_orphan_extensions_pruned(self):
        p=profile(); del p[C]; del p[O]
        self.assertNotIn(S,negotiate(profile(),p))
        self.assertNotIn(F,negotiate(profile(),p))
    def test_multiparent_survives_one_parent(self):
        p=profile(); del p[C]
        self.assertIn(F,negotiate(profile(),p))
        self.assertNotIn(S,negotiate(profile(),p))
    def test_pending_actions_and_failed_complete_schema_valid(self):
        out=self.business.complete()
        self.assertEqual(out['status'],'incomplete')
        self.assertEqual(set(out['actions']),{S,F})
        self.assertEqual(out['messages'][0]['path'],f"$.actions['{S}'][0]")
    def test_extension_rejects_malformed_config_base_accepts_it(self):
        out=self.business.snapshot(); del out['actions'][S][0]['config']['scope_hash']
        Draft202012Validator({'$ref':'https://ucp.dev/schemas/shopping/checkout.json'},registry=REGISTRY).validate(out)
        with self.assertRaises(ValidationError): validate(out,'checkout-composed.json')
    def test_upstream_rejects_invalid_total(self):
        out=self.business.snapshot(); out['totals'].append({'type':'total','amount':1000})
        with self.assertRaises(ValidationError): validate(out,'checkout-composed.json')
    def test_signed_results_complete_checkout(self):
        self.resolve()
        self.assertEqual(self.business.snapshot()['status'],'ready_for_complete')
        out=self.business.complete()
        self.assertEqual(out['status'],'completed')
        self.assertNotIn('actions',out)
        self.assertEqual(out['order']['id'],'ord1')
        self.assertNotIn('actions',self.business.snapshot(now=300))
    def test_tampered_signed_result_rejected(self):
        e=self.provider.issue(self.business.config(S)); e['payload']['expires']=999
        self.assertFalse(self.business.submit(S,e))
    def test_untrusted_provider_rejected(self):
        rogue=Provider('rogue'); e=rogue.issue(self.business.config(S))
        self.assertFalse(self.business.submit(S,e))
    def test_wrong_task_rejected(self):
        c=self.business.config(S); c['task_id']='unrelated-task'
        self.assertFalse(self.business.submit(S,self.provider.issue(c)))
    def test_destination_change_invalidates_evidence(self):
        self.resolve(); e=deepcopy(self.business.accepted[S]); self.business.change(destination='D2')
        self.assertFalse(self.business.submit(S,e))
        self.assertEqual(self.business.complete()['status'],'incomplete')
    def test_payer_change_invalidates_evidence(self):
        self.resolve(); self.business.change(payer='P2')
        self.assertEqual(self.business.snapshot()['status'],'incomplete')
    def test_expiry_rechecked_at_complete(self):
        self.resolve(); self.assertEqual(self.business.complete(now=200)['status'],'incomplete')
    def test_denial_and_pending_do_not_complete(self):
        for result in ['denied','pending']:
            self.assertFalse(self.business.submit(F,self.provider.issue(self.business.config(F),outcome=result)))
        self.assertEqual(self.business.complete()['status'],'incomplete')
    def test_provider_result_not_checkout_state(self):
        e=self.provider.issue(self.business.config(S))
        self.assertEqual(self.business.snapshot()['status'],'incomplete')
        self.business.submit(S,e)
        self.assertIn(F,self.business.snapshot()['actions'])
    def snapshots(self):
        self.resolve(); self.business.complete()
        issued=self.business.document('doc1')
        canceled=self.business.cancel_document('doc1')
        replaced=self.business.document('doc2',replaces='doc1')
        return issued,canceled,replaced
    def test_fiscal_document_delivery_cancel_replace(self):
        issued,canceled,replaced=self.snapshots()
        self.assertEqual([x[F]['revision'] for x in [issued,canceled,replaced]],[1,2,3])
        self.assertEqual(issued[F]['documents'][0]['state'],'certified')
        self.assertEqual(canceled[F]['documents'][0]['state'],'canceled')
        self.assertEqual(replaced[F]['documents'][1]['replaces'],'doc1')
        self.assertEqual(replaced['line_items'][0]['status'],'processing')
        self.assertNotIn('actions',replaced)
    def test_replacement_requires_canceled_original_fixture_rule(self):
        self.resolve(); self.business.complete(); self.business.document('doc1')
        with self.assertRaises(ValueError): self.business.document('doc2',replaces='doc1')
    def test_issued_document_must_match_certification_evidence(self):
        self.resolve(); self.business.complete()
        with self.assertRaises(ValueError): self.business.document('different-document')
    def test_fiscal_document_hash_matches_fixture_bytes(self):
        a,_,_=self.snapshots()
        self.assertEqual(a[F]['documents'][0]['content_hash'],hashlib.sha256(fixture_xml('doc1')).hexdigest())
    def test_duplicate_and_stale_delivery(self):
        a,b,c=self.snapshots(); p=Platform()
        args={'authenticated_business':'business1','expected_business':'business1'}
        self.assertEqual(p.consume_order(c,**args),'applied')
        self.assertEqual(p.consume_order(c,**args),'duplicate')
        self.assertEqual(p.consume_order(a,**args),'stale')
        self.assertEqual(p.latest[F]['revision'],3)
    def test_conflicting_revision_rejected(self):
        a,_,_=self.snapshots(); p=Platform()
        args={'authenticated_business':'business1','expected_business':'business1'}
        p.consume_order(a,**args); changed=deepcopy(a)
        changed[F]['documents'][0]['content_hash']='0'*64
        with self.assertRaises(ValueError): p.consume_order(changed,**args)
    def test_wrong_business_rejected(self):
        a,_,_=self.snapshots()
        with self.assertRaises(ValueError): Platform().consume_order(a,authenticated_business='rogue',expected_business='business1')

if __name__=='__main__': unittest.main(verbosity=2)
