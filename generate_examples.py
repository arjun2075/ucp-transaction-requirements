import json
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from integration import *

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
print('Generated schema-validated Checkout and Order response examples.')
