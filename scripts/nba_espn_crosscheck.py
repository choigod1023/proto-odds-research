"""Verify one archived NBA official HTML against the cached ESPN scoreboard."""
import datetime,hashlib,json,re,subprocess
from pathlib import Path
from nba_espn_research import CACHE,OUT,verified_json
REV='2df626042f633ba08f381f755ccc18f2068733f6'
BASE='experiments/nba-official-information-20261103/'
def blob(path):return subprocess.check_output(['git','show',REV+':'+BASE+path])
def main():
 body=blob('raw/box-0022500274.html');manifest=json.loads(blob('manifest.json'));meta=next(r for r in manifest['requests'] if r.get('file')=='raw/box-0022500274.html');sha=hashlib.sha256(body).hexdigest()
 if sha!=meta['sha256']:raise ValueError('official archive hash mismatch')
 game=json.loads(re.search(rb'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',body).group(1))['props']['pageProps']['game']
 path=CACHE/'scoreboard/20251122.json';receipt=json.loads(path.with_suffix('.receipt.json').read_text(encoding='utf8'));e=next(x for x in verified_json(path)['events'] if x['id']=='401810129');comp={x['homeAway']:x for x in e['competitions'][0]['competitors']}
 expected=[game['homeTeam']['teamTricode'],game['awayTeam']['teamTricode'],game['homeTeam']['score'],game['awayTeam']['score']];actual=[comp['home']['team']['abbreviation'],comp['away']['team']['abbreviation'],int(comp['home']['score']),int(comp['away']['score'])]
 stamp=lambda s:datetime.datetime.fromisoformat(s.replace('Z','+00:00'))
 if expected!=actual or stamp(game['gameTimeUTC'])!=stamp(e['date']):raise ValueError('NBA and ESPN sample differ')
 result={'official_url':meta['url'],'official_game_id':game['gameId'],'official_sha256':sha,'official_received_at':meta['received_at'],'espn_event_id':e['id'],'espn_sha256':receipt['sha256'],'espn_received_at':receipt['received_at'],'utc':e['date'],'home':actual[0],'away':actual[1],'home_score':actual[2],'away_score':actual[3],'matched':True,'scope':'one game only; publisher-wide accuracy not established'}
 (OUT/'official_crosscheck.json').write_text(json.dumps(result,indent=2),encoding='utf8');print(json.dumps(result))
if __name__=='__main__':main()
