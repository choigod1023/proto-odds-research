import hashlib,json,pathlib,subprocess
p=pathlib.Path('experiments/league-research-20261031/football_other')
a=json.loads((p/'audit.json').read_text(encoding='utf-8-sig'))
assert len(a['snapshot_sha256s']) == 41
for name,digest in a['snapshot_sha256s'].items():
 assert hashlib.sha256((pathlib.Path('data/raw/snapshots')/name).read_bytes()).hexdigest()==digest
for c in a['counts'].values():
 assert 0 <= c['price_timestamp_before_t30_uncertified'] <= c['any_three_prices'] <= c['offer_ids']
 assert c['actual_receipt_certified']==c['official_confirmed_player_linked']==0
 assert c['conflicting_recognized_result_labels']==0
print('PASS: 41 input hashes, 5 competition count bounds and recognized-label conflicts; not receipt or model validation')
