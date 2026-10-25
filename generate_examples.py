import json
import hashlib
from pathlib import Path
import tempfile
from cryptography.hazmat.primitives import serialization
from integration import *
from fiscal_ledger import FiscalDocumentLedger

out=ROOT/'examples'; out.mkdir(exist_ok=True)
def save(name,data): (out/name).write_text(json.dumps(data,indent=2)+'\n')
p=Provider('fixture-provider'); b=Business(profile(),p)
(out/'fixture-provider-public-key.pem').write_bytes(p.key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
save('capability-declarations.json',profile())
save('01-checkout-blocked.json',b.complete())
for kind in b.required:
    e=p.issue(b.config(kind)); save(kind+'-signed-result.json',e)
    assert b.submit(kind,e)
save('02-checkout-ready.json',b.snapshot())
b.change(destination='D2')
save('03-checkout-destination-changed.json',b.complete())
for kind in b.required: assert b.submit(kind,p.issue(b.config(kind)))
save('04-checkout-completed.json',b.complete())
save('05-order-document-issued.json',b.document('doc1'))
save('06-order-document-canceled.json',b.cancel_document('doc1'))
save('07-order-document-replaced.json',b.document('doc2',replaces='doc1'))
for id in ['doc1','doc2']: (out/(id+'.xml')).write_bytes(fixture_xml(id))

with tempfile.TemporaryDirectory() as temp:
    ledger=FiscalDocumentLedger(Path(temp)/'ledger.sqlite3',order_id='ord-durable',
        checkout_id='chk-durable',business_id='business1',platform_id='platform1')
    def durable_document(id,replaces=None):
        content=fixture_xml(id)
        document={'id':id,'state':'issued','document_type':'fixture-fiscal-document',
            'jurisdiction':'MX','media_type':'application/xml',
            'content_hash':hashlib.sha256(content).hexdigest(),
            'retrieval_url':f'https://business.example/documents/{id}',
            'issued_at':'2026-10-24T18:00:00Z'}
        if replaces: document['replaces']=replaces
        return document,content
    doc1,content1=durable_document('doc1')
    issued=ledger.apply({'event_id':'evt-issue','order_id':'ord-durable',
        'checkout_id':'chk-durable','expected_revision':0,'type':'issue',
        'occurred_at':'2026-10-24T18:00:00Z','document':doc1},content=content1,
        authenticated_business='business1')
    canceled=ledger.apply({'event_id':'evt-cancel','order_id':'ord-durable',
        'checkout_id':'chk-durable','expected_revision':1,'type':'cancel',
        'occurred_at':'2026-10-24T18:05:00Z','document_id':'doc1'},content=None,
        authenticated_business='business1')
    doc2,content2=durable_document('doc2','doc1')
    replaced=ledger.apply({'event_id':'evt-replace','order_id':'ord-durable',
        'checkout_id':'chk-durable','expected_revision':2,'type':'replace',
        'occurred_at':'2026-10-24T18:10:00Z','document':doc2},content=content2,
        authenticated_business='business1')
    save('08-durable-document-issued.json',issued)
    save('09-durable-document-canceled.json',canceled)
    save('10-durable-document-replaced.json',replaced)
print('Generated schema-validated Checkout and Order response examples.')
