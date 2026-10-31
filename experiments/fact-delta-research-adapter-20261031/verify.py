"""Independent serialized fixture audit; not evidence of real-source validity."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
HERE=Path(__file__).resolve().parent
x=json.loads((HERE/'example-bundle.json').read_text(encoding='utf8'))
p=subprocess.run([sys.executable,'-X','utf8',str(HERE/'run_fixture.py'),str(HERE/'example-bundle.json')],capture_output=True,text=True,encoding='utf8',check=True)
y=json.loads(p.stdout); recorded=y.pop('research_envelope_sha256')
canonical=json.dumps(y,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
assert hashlib.sha256(canonical.encode('utf8')).hexdigest()==recorded
assert [Decimal(str(v)) for v in y['probability_vector']]==[Decimal(str(v)) for v in x['baseline']['probability_vector']]
assert y['baseline_freeze_hash']==x['baseline']['freeze_hash'] and y['baseline_input_content_sha256']==x['baseline']['input_content_sha256']
assert y['external_receipt_verified'] is False and y['prediction_changed'] is False
assert y['features'][0]['after_value']==x['facts'][0]['after_value']
print(json.dumps({'synthetic_fixtures_checked':1,'probability_changed':False,'external_receipt_verified':False,'hash_match':True}))
