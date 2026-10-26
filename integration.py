"""Experimental extension integration. No legal-rule selection or live provider."""
import base64
from copy import deepcopy
import hashlib
import json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from schema_support import REGISTRY, ROOT, validate, validate_declarations

S='com.example.screening'
F='com.example.fiscal_documents'
C='dev.ucp.shopping.checkout'
O='dev.ucp.shopping.order'
V='2026-10-23'  # Fixture metadata date, not a certified UCP release assertion.

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()

def fixture_xml(id):
    return f'<FixtureFiscalDocument id="{id}" notice="NOT A CFDI"/>'.encode()

def profile():
    names={C:None,O:None,S:C,F:[C,O]}
    out={}
    for name,parent in names.items():
        entry={'version':V,'spec':f'https://example.com/spec/{name}',
               'schema':f'https://example.com/schemas/{name}.json'}
        if parent: entry['extends']=parent
        out[name]=[entry]
    return out

def negotiate(business,platform):
    """Exact-version intersection and orphan pruning; one version per fixture."""
    selected={}
    for name,entries in business.items():
        matches={x['version'] for x in platform.get(name,[])}
        selected[name]=[deepcopy(x) for x in entries if x['version'] in matches]
    selected={k:v for k,v in selected.items() if v}
    while True:
        keep={}
        for name,entries in selected.items():
            entry=entries[0]
            parents=entry.get('extends',[])
            if isinstance(parents,str): parents=[parents]
            if not parents or any(p in selected for p in parents): keep[name]=entries
        if keep==selected: return keep
        selected=keep

TOTALS=[{'type':'subtotal','amount':1000},{'type':'total','amount':1000}]
ITEM={'id':'sku1','title':'Fixture goods','price':1000}

class Provider:
    def __init__(self,name):
        self.name=name
        self.key=Ed25519PrivateKey.generate()
    def issue(self,config,*,expires=200,outcome='satisfied'):
        payload={**deepcopy(config),'expires':expires,'outcome':outcome}
        payload['provider_id']=self.name
        if payload['policy_id'].startswith(F):
            payload['document_id']='doc1'
            payload['document_hash']=hashlib.sha256(fixture_xml('doc1')).hexdigest()
        return {'payload':payload,'signature':base64.b64encode(self.key.sign(canonical(payload))).decode()}

class Business:
    def __init__(self,platform,provider,required=(S,F)):
        self.active=negotiate(profile(),platform)
        self.required=required
        self.provider=provider
        self.trust={provider.name:provider.key.public_key()}
        self.facts={'buyer':'A','seller':'B','payer':'P','destination':'D','currency':'MXN','total':1000,
                    'goods':[{'id':'sku1','quantity':1}]}
        self.accepted={}
        self.task_revision=1
        self.state='incomplete'
        self.documents=[]
        self.document_events=[]
        self.doc_revision=0
    def config(self,kind):
        return {'checkout_id':'chk1','scope_hash':hashlib.sha256(canonical(self.facts)).hexdigest(),
                'policy_id':kind+'-fixture-policy-v1','provider_id':self.provider.name,
                'task_id':f'{kind}-{self.task_revision}'}
    def accepts(self,kind,evidence,now):
        try:
            validate(evidence,'evidence.json')
            payload=evidence['payload']
            key=self.trust[payload['provider_id']]
            key.verify(base64.b64decode(evidence['signature'],validate=True),canonical(payload))
            if kind==F and (not payload.get('document_id') or not payload.get('document_hash')):
                return False
            return (all(payload[k]==v for k,v in self.config(kind).items())
                    and payload['expires']>now and payload['outcome']=='satisfied')
        except Exception:
            return False
    def submit(self,kind,evidence,now=100):
        if self.state=='completed': return False
        if kind not in self.active or not self.accepts(kind,evidence,now): return False
        self.accepted[kind]=deepcopy(evidence)
        return True
    def change(self,**facts):
        if self.state=='completed': raise ValueError('Checkout already completed')
        self.facts.update(facts)
        self.task_revision+=1
        self.accepted.clear()
        self.state='incomplete'
    def snapshot(self,now=100):
        missing=[x for x in self.required if x not in self.active]
        unresolved=[x for x in self.required if x in self.active and
                    not self.accepts(x,self.accepted.get(x),now)]
        if self.state=='completed': missing=[]; unresolved=[]
        blocked=bool(missing or unresolved)
        if self.state!='completed': self.state='incomplete' if blocked else 'ready_for_complete'
        out={'ucp':{'version':V,'status':'success','capabilities':{
            k:[{'version':v[0]['version']}] for k,v in self.active.items()},'payment_handlers':{}},
            'id':'chk1','line_items':[{'id':'li1','item':deepcopy(ITEM),'quantity':1,'totals':deepcopy(TOTALS)}],
            'status':self.state,'currency':'MXN','totals':deepcopy(TOTALS),'links':[]}
        if unresolved:
            out['actions']={x:[{'id':self.config(x)['task_id'],'config':self.config(x)}] for x in unresolved}
        if missing:
            out['messages']=[{'type':'error','code':'com.example.required_workflow_unavailable',
                              'severity':'recoverable','content':'Required workflow unavailable; use a compliant alternative or stop.'}]
        if self.state=='completed':
            out['order']={'id':'ord1','permalink_url':'https://business.example/orders/ord1'}
        validate(out,'checkout-composed.json')
        return out
    def complete(self,now=100):
        out=self.snapshot(now)
        if out['status']!='ready_for_complete':
            if out.get('actions'):
                kind=next(iter(out['actions']))
                out['messages']=[{'type':'error','code':'com.example.verification_pending',
                                  'severity':'recoverable','content':'Required verification is outstanding.',
                                  'path':f"$.actions['{kind}'][0]"}]
            validate(out,'checkout-composed.json')
            return out
        self.state='completed'
        return self.snapshot(now)
    def document(self,id,*,replaces=None):
        if self.state!='completed': raise ValueError('Order not placed')
        if any(x['id']==id for x in self.documents): raise ValueError('Duplicate document ID')
        if not self.documents and F in self.required:
            payload=self.accepted[F]['payload']
            if payload['document_id']!=id or payload['document_hash']!=hashlib.sha256(fixture_xml(id)).hexdigest():
                raise ValueError('Document does not match accepted fiscal certification evidence')
        if replaces:
            old=next(x for x in self.documents if x['id']==replaces)
            if old['state']!='canceled': raise ValueError('Fixture requires accepted cancellation first')
        doc={'id':id,'state':'issued','document_type':'fixture-fiscal-document',
             'jurisdiction':'MX','media_type':'application/xml','issued_at':'2026-10-24T18:00:00Z',
             'content_hash':hashlib.sha256(fixture_xml(id)).hexdigest(),
             'retrieval_url':f'https://business.example/documents/{id}'}
        if replaces: doc['replaces']=replaces
        self.documents.append(doc)
        self.doc_revision+=1
        self.document_events.append({'id':f'doc-event-{self.doc_revision}',
                                     'type':'replace' if replaces else 'issue',
                                     'occurred_at':'2026-10-24T18:00:00Z','document_id':id})
        return self.order()
    def cancel_document(self,id):
        doc=next(x for x in self.documents if x['id']==id)
        if doc['state']!='canceled':
            doc['state']='canceled'
            self.doc_revision+=1
            self.document_events.append({'id':f'doc-event-{self.doc_revision}','type':'cancel',
                                         'occurred_at':'2026-10-24T18:00:00Z','document_id':id})
        return self.order()
    def order(self):
        out={'ucp':{'version':V,'status':'success','capabilities':{
            O:[{'version':V}],F:[{'version':V}]}},'id':'ord1','checkout_id':'chk1',
            'permalink_url':'https://business.example/orders/ord1',
            'line_items':[{'id':'li1','item':deepcopy(ITEM),
                'quantity':{'original':1,'total':1,'fulfilled':0},'status':'processing','totals':deepcopy(TOTALS)}],
            'fulfillment':{'expectations':[],'events':[]},'currency':'MXN','totals':deepcopy(TOTALS),
            F:{'revision':self.doc_revision,'documents':deepcopy(self.documents),
               'events':deepcopy(self.document_events)}}
        validate(out,'order-composed.json')
        return out

class Platform:
    def __init__(self): self.latest=None
    def consume_order(self,snapshot,*,authenticated_business,expected_business):
        # Authentication result supplied by transport adapter; RFC 9421 not implemented here.
        if authenticated_business!=expected_business: raise ValueError('Wrong order owner')
        validate(snapshot,'order-composed.json')
        if self.latest:
            if snapshot['id']!=self.latest['id']: raise ValueError('Wrong order')
            new=snapshot[F]['revision']; old=self.latest[F]['revision']
            if new<old: return 'stale'
            if new==old:
                if snapshot!=self.latest: raise ValueError('Conflicting same-revision snapshot')
                return 'duplicate'
        self.latest=deepcopy(snapshot)
        return 'applied'
