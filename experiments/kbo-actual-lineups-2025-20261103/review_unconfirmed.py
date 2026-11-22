"""Offline check of the five unconfirmed lineup requests; never infer first nine."""
import gzip,json
from pathlib import Path
from collect import HERE,STATE,sha,fidpath

def main():
    result=[]
    for path in sorted((STATE/'stage1').glob('*.json')):
        one=json.loads(path.read_bytes())
        if one.get('lineup_flag') is not False or not one.get('schedule_game'):continue
        gid=one['official_game_id'];g=one['schedule_game'];sched_meta=one['schedule_evidence'];line_meta=one['lineup_evidence']
        raw=gzip.decompress((STATE/sched_meta['raw_file']).read_bytes());assert sha(raw)==sched_meta['sha256'];schedule=json.loads(raw)
        games=[x for x in schedule['game'] if x.get('G_ID')==gid]
        if len(games)!=1 or str(games[0]['G_DT'])!=one['date']:raise ValueError('official_schedule_identity_mismatch')
        expected={'leId':g['LE_ID'],'srId':g['SR_ID'],'seasonId':g['SEASON_ID'],'gameId':gid}
        if line_meta['params']!=expected:raise ValueError('lineup_request_identity_mismatch')
        lraw=gzip.decompress((STATE/line_meta['raw_file']).read_bytes());assert sha(lraw)==line_meta['sha256'];flag=json.loads(lraw)[0][0]['LINEUP_CK']
        if flag is not False:raise ValueError('lineup_not_false')
        two=json.loads(fidpath('stage2',one['fixture_id']).read_bytes());bmeta=two['box_evidence']
        if bmeta['params']!=expected:raise ValueError('box_request_identity_mismatch')
        braw=gzip.decompress((STATE/bmeta['raw_file']).read_bytes());assert sha(braw)==bmeta['sha256'];box=json.loads(braw)
        flags=[];starter_text=False
        for table in box['tables'][1:3]:
            starter_text |= '선발' in json.dumps(table,ensure_ascii=False)
            for row in table.get('rows',[]):
                for cell in [row]+row.get('row',[]):
                    flags += [k for k,v in cell.items() if any(w in k.lower() for w in ('start','lineup','flag')) and v not in (None,'')]
        result.append({'fixture_id':one['fixture_id'],'official_game_id':gid,'date':one['date'],'official_schedule_unique':True,
          'league_id':g['LE_ID'],'series_id':g['SR_ID'],'season_id':g['SEASON_ID'],'lineup_request':line_meta['params'],
          'box_request':bmeta['params'],'lineup_confirmed_flag':False,'hitter_box_explicit_starter_text':starter_text,
          'hitter_box_explicit_starter_fields':sorted(set(flags)),'schedule_sha256':sched_meta['sha256'],'lineup_sha256':line_meta['sha256'],'box_sha256':bmeta['sha256'],
          'decision':'excluded_unconfirmed_no_explicit_starting_hitter_marker_in_inspected_box_tables'})
    (HERE/'unconfirmed-review.json').write_bytes((json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode())
    print(json.dumps({'reviewed':len(result),'explicit_hitter_starter_markers':sum(x['hitter_box_explicit_starter_text'] or bool(x['hitter_box_explicit_starter_fields']) for x in result)}))
if __name__=='__main__':main()
