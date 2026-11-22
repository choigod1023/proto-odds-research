"""Bounded official Daily.aspx identity-only check for two same-name pitchers."""
import json,time
from datetime import datetime,timezone
from bs4 import BeautifulSoup
from collect import Capture,ROOT,Stop,key

URL=ROOT+'/Record/Player/PitcherDetail/Daily.aspx'
def main():
    c=Capture({'phase':'pitcher_identity_review','denominator':2})
    for pid in ('51454','60146'):
        raw,getmeta=c.fetch('GET',URL,{'playerId':pid});doc=BeautifulSoup(raw.decode('utf8'),'html.parser');form=doc.select_one('form#mainForm');year=doc.select_one('select[id$=ddlYear]');series=doc.select_one('select[id$=ddlSeries]')
        if not form or not year or not series:raise ValueError('official_public_form_missing')
        # Hidden ASP.NET state is transmitted as supplied by the public page,
        # but is never copied into the request log or printed.
        fields={e['name']:e.get('value','') for e in form.select('input[type=hidden][name]')}
        fields['__EVENTTARGET']=series['name'];fields['__EVENTARGUMENT']='';fields[year['name']]='2025';fields[series['name']]='0'
        public={'playerId':pid,'year':'2025','series':'0','event':'series_select','form_source_sha256':getmeta['sha256']}
        k=key('POST',URL,public)
        if k in c.cache:print(pid,'already cached');continue
        for attempt in range(2):
            if c.count>=2400:raise Stop('request_cap')
            wait=max(0,1.5-(time.monotonic()-c.last))
            if wait:time.sleep(wait)
            c.count+=1;c.update(request_key=k,request_in_progress=True)
            record={'request_key':k,'origin':'new_request','method':'POST','url':URL,'params':public,'attempt':attempt+1,'hidden_form_state_recorded':False}
            try:
                r=c.s.post(URL,params={'playerId':pid},data=fields,timeout=(5,15),allow_redirects=False);body=r.content;record.update(http_status=r.status_code,received_at=datetime.now(timezone.utc).isoformat());c.append(record,body)
            except Exception as e:
                record.update(http_status=None,failed_at=datetime.now(timezone.utc).isoformat(),error_type=type(e).__name__);c.append(record);r=None
            finally:c.last=time.monotonic();c.update(request_in_progress=False)
            if r is not None and r.status_code==200:print(pid,'HTTP',r.status_code,'sha256',record['sha256']);break
            if r is not None and r.status_code!=429:raise Stop('http_'+str(r.status_code))
            if attempt==0:time.sleep(15)
        else:raise Stop('timeout_or_429_after_backoff')

if __name__=='__main__':main()
