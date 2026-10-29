"""Restore missing 2023 starter identities and true pitcher stats as past history."""
from collections import Counter
from pathlib import Path
import argparse
import json

TEAMS={'北海道日本ハムファイターズ':'닛폰햄','東北楽天ゴールデンイーグルス':'라쿠텐',
    '読売ジャイアンツ':'요미우리','中日ドラゴンズ':'주니치','福岡ソフトバンクホークス':'소프트뱅크',
    '千葉ロッテマリーンズ':'지바롯데','埼玉西武ライオンズ':'세이부','オリックス・バファローズ':'오릭스',
    '東京ヤクルトスワローズ':'야쿠르트','広島東洋カープ':'히로시마','阪神タイガース':'한신','横浜DeNAベイスターズ':'요코하마'}


def reconcile(boxes,history):
    groups={};excluded=Counter()
    for b in boxes:
        if not b['accepted']:excluded['fetch_or_cancelled_or_parse_failure']+=1;continue
        sides={TEAMS.get(name):p for name,p in b['pitching'].items()}
        if not sides.get(b['home']) or not sides.get(b['away']):excluded['unmatched_native_team']+=1;continue
        k=(b['date'],b['home'],b['away'])
        if k in groups:groups[k]=None
        else:groups[k]=(b,sides)
    out=[]
    for row in history:
        if row['league']!='NPB' or not row['date'].startswith('2023'):continue
        match=groups.get((row['date'],row['home'],row['away']))
        if not match:excluded['missing_or_ambiguous_box']+=1;continue
        b,sides=match
        hg=sum(int(p['runs']) for p in sides[row['away']]);ag=sum(int(p['runs']) for p in sides[row['home']])
        if (row['hg'],row['ag'])!=(hg,ag):excluded['official_archive_score_disagreement']+=1;continue
        r=dict(row,actual_starters=[sides[row[s]][0]['name'] for s in ['home','away']],
            official_pitching={s:sides[row[s]] for s in ['home','away']},official_box_url=b['url'],
            source_kind='postgame_training_history_not_pregame_announcement',starter_identity_namespace='NPB_official_native_name')
        out.append(r)
    return out,dict(excluded)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True);p.add_argument('--prior',type=Path,required=True);a=p.parse_args()
    load=lambda f:[json.loads(s) for s in f.read_text(encoding='utf8').splitlines() if s]
    boxes=load(a.folder/'npb-2023-boxes.jsonl');out,ex=reconcile(boxes,load(a.prior/'history.jsonl'))
    (a.folder/'npb-2023-restored.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in out),encoding='utf8',newline='\n')
    report=dict(attempted_official_boxes=len(boxes),parsed_official_boxes=sum(r['accepted'] for r in boxes),
        restored_games=len(out),restored_starter_sides=2*len(out),excluded=ex,
        pregame_publication_certified=False,
        limitation='Native names/player URLs are retained; 2024+ Korean-name identities still require explicit reconciliation before fitting personal cross-season history.')
    (a.folder/'npb-restoration-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(report,ensure_ascii=False))
