import requests,datetime,hashlib,json,pathlib
from bs4 import BeautifulSoup
from urllib.parse import urljoin
out=pathlib.Path('experiments/npb-recovery-20261102/raw'); out.mkdir(parents=True,exist_ok=True)
def get(url):
 r=requests.get(url,timeout=15); now=datetime.datetime.now(datetime.timezone.utc).isoformat(); r.raise_for_status(); h=hashlib.sha256(r.content).hexdigest(); (out/(h+'.html')).write_bytes(r.content); (out/(h+'.json')).write_text(json.dumps({'url':url,'observed_at':now,'status':r.status_code,'sha256':h,'bytes':len(r.content)},ensure_ascii=False,indent=2),encoding='utf8'); print(url,h,now); return r
get('https://npb.jp/bis/2023/games/s2023033001315.html')
u='https://npb.jp/bis/2023/games/gm20230331.html'; r=get(u); soup=BeautifulSoup(r.content,'html.parser'); urls=list(dict.fromkeys(urljoin(u,a['href']) for a in soup.find_all('a',href=True) if a['href'].startswith('s20230331'))); print('links',len(urls));
for url in urls[:2]: get(url)
