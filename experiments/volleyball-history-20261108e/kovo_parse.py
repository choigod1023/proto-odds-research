"""One-at-a-time read-only KOVO result collector; no DB, scheduler or AI calls."""
import argparse,base64,hashlib,json,re
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from bs4 import BeautifulSoup

def parse(body,url):
 u=urlparse(url);qs=parse_qs(u.query)
 if u.scheme!='https' or u.netloc!='dbbank.kovo.co.kr' or u.path!='/game/11110_pop_result.asp' or len(qs.get('PID',[]))!=1:raise ValueError('unsupported_url')
 pid=qs['PID'][0]
 try: identity=base64.b64decode(pid[::-1],validate=True).decode('ascii').strip();season,competition,game=identity.split(':')
 except Exception as exc:raise ValueError('invalid_pid') from exc
 if not (season.isdigit() and competition.isdigit() and game.isdigit()):raise ValueError('invalid_pid_parts')
 soup=BeautifulSoup(body,'html.parser');head=soup.find('h3');tables=soup.find_all('table')
 if not head or len(tables)<4:raise ValueError('missing_result_tables')
 dm=re.search(r'(\d{4})\.(\d{2})\.(\d{2})',head.get_text(' ',strip=True))
 if not dm:raise ValueError('missing_date')
 def cells(tr):return [c.get_text(' ',strip=True) for c in tr.find_all(['td','th'])]
 rows=tables[0].find_all('tr');a=cells(rows[1]);b=cells(rows[2]);info=cells(tables[1].find('tr'))
 if len(a)!=9 or len(b)!=7 or info[:2]!=['경기번호',str(int(game))]:raise ValueError('unexpected_layout_or_game_id')
 h=[int(v) if v else None for v in a[2:7]];w=[int(v) if v else None for v in b[1:6]]
 pairs=[(x,y) for x,y in zip(h,w) if x is not None and y is not None]
 if any((x is None)!=(y is None) for x,y in zip(h,w)) or len(pairs) not in (3,4,5) or sum(hh for hh,_ in pairs)!=int(a[7]) or sum(aa for _,aa in pairs)!=int(b[6]):raise ValueError('invalid_set_scores')
 sets=[sum(x>y for x,y in pairs),sum(y>x for x,y in pairs)]
 if sets!=[int(a[0]),int(a[8])] or max(sets)!=3 or min(sets)>2 or any(x==y for x,y in pairs):raise ValueError('inconsistent_match_result')
 players=[];team_totals=[]
 for table in tables[2:4]:
  links=table.select('a[href*="pcode="]');players.append({'listed_players':len(links),'player_ids':[parse_qs(urlparse(x.get('href')).query).get('pcode',[None])[0] for x in links]})
  total=next((r for r in table.find_all('tr') if r.find('td',colspan='9') and '합계' in r.get_text()),None)
  if not total:raise ValueError('missing_team_totals')
  vals=cells(total)
  if len(vals)!=13:raise ValueError('unexpected_team_totals')
  team_totals.append({'points':vals[1],'attack_front_points':vals[2],'attack_back_points':vals[3],'block_points':vals[4],'serve_points':vals[5],'player_errors':vals[6],'attack_success_pct':vals[8],'attack_efficiency_pct':vals[9],'effective_blocks':vals[11],'receive_efficiency_pct':vals[12]})
 return {'pid':pid,'season_code':season,'competition_code':competition,'official_game_number':int(game),'date':'-'.join(dm.groups()),'local_start':cells(tables[1].find_all('tr')[1])[1],'local_end':cells(tables[1].find_all('tr')[1])[3],'teams':[a[1],b[0]],'set_points':pairs,'sets_won':sets,'player_lists':players,'team_totals':team_totals,'status':'score_indicates_complete','raw_sha256':hashlib.sha256(body).hexdigest(),'source_url':url}

def main():
 p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--output',required=True);args=p.parse_args()
 u=urlparse(args.url)
 if u.scheme!='https' or u.netloc!='dbbank.kovo.co.kr' or u.path!='/game/11110_pop_result.asp':raise ValueError('unsupported_url')
 import requests
 r=requests.get(args.url,timeout=20,allow_redirects=False);received=datetime.now(timezone.utc).isoformat();r.raise_for_status()
 if len(r.content)>1_000_000 or not 200<=r.status_code<300:raise ValueError('bad_response')
 result=parse(r.content,args.url);out=Path(args.output);out.mkdir(parents=True,exist_ok=False);(out/'response.html').write_bytes(r.content);(out/'receipt.json').write_text(json.dumps({'url':args.url,'http_status':r.status_code,'received_at_utc':received,'sha256':result['raw_sha256'],'bytes':len(r.content)},ensure_ascii=False,indent=2),encoding='utf8');(out/'parsed.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
if __name__=='__main__':main()
