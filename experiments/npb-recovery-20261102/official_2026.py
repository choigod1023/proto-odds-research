import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from recover import score

TEAMS={'読売ジャイアンツ':'g','阪神タイガース':'t','横浜DeNAベイスターズ':'db','中日ドラゴンズ':'d','広島東洋カープ':'c','東京ヤクルトスワローズ':'s','福岡ソフトバンクホークス':'h','北海道日本ハムファイターズ':'f','オリックス・バファローズ':'b','東北楽天ゴールデンイーグルス':'e','埼玉西武ライオンズ':'l','千葉ロッテマリーンズ':'m'}
def daily_pairs_2026(raw, day):
 soup=BeautifulSoup(raw,'html.parser'); out=[]
 for a in soup.find_all('a',href=True):
  m=re.fullmatch(r'/scores/2026/(\d{4})/([a-z]+)-([a-z]+)-(\d+)/',urlparse(a['href']).path)
  if m and m[1]==day[5:7]+day[8:10]:out.append(('https://npb.jp'+m[0]+'box.html',(m[2],m[3])))
 return out

def parse_2026(raw,url,day):
 soup=BeautifulSoup(raw,'html.parser'); text=soup.get_text(' ',strip=True)
 match=re.fullmatch(r'/scores/2026/(\d{4})/([a-z]+)-([a-z]+)-(\d+)/box.html',urlparse(url).path)
 if not match or match[1]!=day[5:7]+day[8:10] or '試合終了' not in text:return None
 dm=re.search(r'2026年\s*(\d+)月\s*(\d+)日',text)
 if not dm or (int(dm[1]),int(dm[2]))!=(int(day[5:7]),int(day[8:10])):return None
 home,away=match[2],match[3]; players={}
 for side,code in (('t',away),('b',home)):
  table=soup.select_one('#tablefix_'+side+'_p')
  heading=table.find_previous(['h3','h4','h5']) if table else None
  if not table or not heading or TEAMS.get(heading.get_text(' ',strip=True))!=code:return None
  rows=table.find_all('tr'); headers=[c.get_text(' ',strip=True) for c in rows[0].find_all(['td','th'],recursive=False)]
  if '投手' not in headers:return None
  i=headers.index('投手'); player=None
  for tr in rows[1:]:
   cells=tr.find_all(['td','th'],recursive=False)
   if len(cells)!=len(headers):continue
   if not cells[i].get_text(' ',strip=True):continue
   a=cells[i].find('a',href=True); m=re.fullmatch(r'/bis/players/(\d+)\.html',urlparse(a['href']).path) if a else None
   if not a or not m:return None
   player={'name':a.get_text(' ',strip=True),'player_id':m[1],'player_url':'https://npb.jp'+urlparse(a['href']).path,'team_id':code,'role':'starter'};break
  if not player:return None
  players[code]=player
 start=re.search(r'◇開始\s*(\d{1,2}):(\d{2})',text)
 final_score=score(raw)
 if not start or final_score is None:return None
 return {'date':day,'official_game_page_url':url,'official_home_team_id':home,'official_away_team_id':away,'home_starter_name':players[home]['name'],'away_starter_name':players[away]['name'],'home_starter_player':players[home],'away_starter_player':players[away],'official_start_hhmm':f'{int(start[1]):02d}:{start[2]}','home_score':final_score[0],'away_score':final_score[1]}
