"""Public historical Proto prices; isolated research, never production DB access.

One sequential standard-client GET stream, >=2.5s after each response. No auth,
redirects, retries or alternate endpoints. Failures are persistent; HTTP 403
stops the entire run and prevents subsequent network requests on resume.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.parse import parse_qs, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wisetoto import GameRow, _SEQ_RE, parse_rows  # noqa: E402
from matches import clean_team  # noqa: E402
from bets import winner_index  # noqa: E402

OUT = ROOT / 'experiments/tipster-year-20261028/prices'
START, END = '2025-10-28', '2026-10-27'
LEAGUES = {'K리그1', 'K리그2', 'KBO', 'NPB', 'MLB', 'NBA', 'KBL'}
BASE = 'https://www.wisetoto.com'


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def append(path, value):
    with path.open('a', encoding='utf8', newline='\n') as f:
        f.write(dumps(value) + '\n')


def read_lines(path):
    return [json.loads(s) for s in path.read_text(encoding='utf8').splitlines() if s] if path.exists() else []


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def decode(raw):
    # Existing endpoint is UTF-8. Fail closed instead of replacement decoding.
    return raw.decode('utf8', errors='strict')


def sequence(html, year, rnd):
    matches = {(int(y), int(r), s) for y, r, s in _SEQ_RE.findall(html)}
    if len(matches) != 1:
        raise ValueError('missing or conflicting archive identity')
    y, r, seq = next(iter(matches))
    if (y, r) != (year, rnd) or seq == '0':
        raise ValueError('requested year/round mismatch or zero SEQ')
    return seq


def game_date(g):
    m = re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})', g.date_text)
    if not m:
        raise ValueError('unparseable kickoff')
    month, day, hour, minute = map(int, m.groups())
    year = g.year - 1 if g.round == 1 and month == 12 else g.year
    # Last annual round can include January fixtures of the following year.
    if g.round >= 150 and month == 1:
        year = g.year + 1
    return datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=9)))


def normalize(g, meta):
    if g.league not in LEAGUES:
        return None, 'other_league'
    if g.market_tag != 'hm' or g.market_family not in {'승패', '승무패'} or g.market_label or g.is_first_half:
        return None, 'not_full_moneyline'
    if g.is_void:
        return None, 'void'
    expected = 3 if g.sport == 'sc' else 2
    if len(g.odds) != expected or any(not math.isfinite(o) or o <= 1 for o in g.odds):
        return None, 'incomplete_or_invalid_vector'
    try:
        dt = game_date(g)
    except ValueError:
        return None, 'invalid_kickoff'
    if not START <= dt.date().isoformat() <= END:
        return None, 'outside_window'
    hs = re.fullmatch(r'(.+?)\s+(\d+)', g.home.strip())
    aws = re.fullmatch(r'(\d+)\s+(.+)', g.away.strip())
    wi = winner_index(g.n_way, g.result)
    if not hs or not aws or wi is None:
        return None, 'missing_archive_final_score_or_result'
    home_score, away_score = int(hs[2]), int(aws[1])
    expected_winner = (0 if home_score > away_score else (2 if g.n_way == 3 else 1))
    if home_score == away_score:
        expected_winner = 1 if g.n_way == 3 else None
    if wi != expected_winner:
        return None, 'score_result_disagreement'
    return dict(league=g.league, date=dt.date().isoformat(), kickoff=dt.isoformat(),
                timezone='Asia/Seoul', home=clean_team(g.home), away=clean_team(g.away),
                home_score=home_score, away_score=away_score, odds=g.odds,
                odds_order=['home', 'draw', 'away'] if g.n_way == 3 else ['home', 'away'],
                winner=wi, market='full_game_moneyline', raw_year=g.year,
                raw_round=g.round, raw_game_no=g.game_no, raw_date_text=g.date_text,
                raw_result=g.result, **meta,
                quote_quality='retrospective_archive_not_purchase_time_certified',
                score_source_quality='proto_archive_only_not_league_official',
                official_result_verified=False), None


def summary(out):
    offers = read_lines(out/'offers.jsonl')
    rounds = read_lines(out/'rounds.jsonl')
    requests_ = read_lines(out/'requests.jsonl')
    month_counts = defaultdict(Counter)
    unique = defaultdict(set)
    for o in offers:
        month_counts[o['date'][:7]][o['league']] += 1
        unique[o['date'][:7]].add((o['league'], o['kickoff'], o['home'], o['away']))
    days = {o['date'] for o in offers}
    gaps = []
    dt = date.fromisoformat(START)
    while dt <= date.fromisoformat(END):
        if dt.isoformat() not in days:
            gaps.append(dt.isoformat())
        dt += timedelta(days=1)
    months = sorted({(date(2025, 10, 1)+timedelta(days=i)).strftime('%Y-%m')
                     for i in range(365+31) if '2025-10' <= (date(2025,10,1)+timedelta(days=i)).strftime('%Y-%m') <= '2026-10'})
    planned = [(2025,r) for r in range(120,155)]+[(2026,r) for r in range(1,128)]
    completed = {(r['year'],r['round']) for r in rounds if r['status']=='ok'}
    attempted = {(r['year'],r['round']) for r in rounds}
    result = dict(generated_at=now(), window=[START, END], leagues=sorted(LEAGUES),
                  planned_rounds=len(planned), successful_rounds=len(completed),
                  failed_rounds=[r for r in rounds if r['status']!='ok'],
                  unattempted_rounds=[list(r) for r in planned if r not in attempted],
                  network_requests=len(requests_),
                  discovery_requests=1 if (out/'discovery.json').exists() else 0,
                  network_requests_including_discovery=len(requests_)+(1 if (out/'discovery.json').exists() else 0),
                  offers=len(offers),
                  provenance=dict(Counter(o['acquisition'] for o in offers)),
                  monthly=[dict(month=m, offers=sum(month_counts[m].values()),
                                unique_fixtures=len(unique[m]), by_league=dict(month_counts[m])) for m in months],
                  by_league=dict(Counter(o['league'] for o in offers)),
                  no_offer_dates=gaps, no_offer_dates_caveat='No offers is not proof that fixtures existed or were omitted.',
                  date_min=min(days,default=None),date_max=max(days,default=None),
                  official_result_verified=False,
                  scope='Seven target leagues; archive prices and archive scores only. Parent joins independent official results and tipsters.')
    write_json(out/'coverage.json',result)
    return result


class Collector:
    def __init__(self, out):
        self.out = out
        self.session = requests.Session()
        self.session.headers.update({'User-Agent':'sportstoto-research/1.0',
                                     'Referer':BASE+'/index.htm','X-Requested-With':'XMLHttpRequest'})
        self.last = time.monotonic()  # includes wait before first request on resume

    def get(self, stage, year, rnd, params):
        if any(r.get('status') == 403 for r in read_lines(self.out/'requests.jsonl')):
            raise RuntimeError('persistent HTTP403 stop; no more network access')
        time.sleep(max(0, 2.5-(time.monotonic()-self.last)))
        path = '/index.htm' if stage == 'index' else '/util/gameinfo/get_proto_list.htm'
        meta = dict(stage=stage,year=year,round=rnd,observed_at=now())
        try:
            response = self.session.get(BASE+path,params=params,timeout=(5,18),allow_redirects=False)
            self.last = time.monotonic()
            meta.update(status=response.status_code,url=response.url,sha256=digest(response.content),bytes=len(response.content))
            cache = self.out/'raw'/f'{year}-{rnd:03}-{stage}.bin'
            cache.write_bytes(response.content)
            append(self.out/'requests.jsonl',meta)
            if response.status_code != 200:
                raise RuntimeError(f'HTTP{response.status_code}; host stopped; no retries')
            return response.content, meta
        except requests.RequestException as exc:
            self.last = time.monotonic()
            meta.update(error=type(exc).__name__,message=str(exc)[:250])
            append(self.out/'requests.jsonl',meta)
            raise

    def collect_round(self, year, rnd):
        index, im = self.get('index', year, rnd,dict(tab_type='proto',game_type='pt',game_category='pt1',game_year=year,game_round=rnd))
        seq = sequence(decode(index), year, rnd)
        raw, rm = self.get('list',year,rnd,dict(game_category='pt1',game_year=year,game_round=rnd,
                          game_month='',game_day='',game_info_master_seq=seq,sports='',sort='',tab_type='proto'))
        rows = parse_rows(decode(raw),year,rnd)
        if not rows:
            raise ValueError('validated SEQ returned zero game rows')
        meta = dict(source_url=rm['url'],response_sha256=rm['sha256'],observed_at=rm['observed_at'],
                    index_sha256=im['sha256'],raw_master_seq=seq,acquisition='new_public_archive')
        accepted, rejected, dates = [], Counter(), []
        for g in rows:
            try:
                dates.append(game_date(g).date().isoformat())
            except ValueError:
                pass
            offer, why = normalize(g, meta)
            rejected[why or 'accepted'] += 1
            # Parsed facts sufficient for offline normalization replay, including rejected MLs.
            if g.league in LEAGUES and g.market_tag == 'hm' and not g.market_label and g.market_family in {'승패','승무패'}:
                append(self.out/'parsed-facts.jsonl',dict(row=asdict(g),meta=meta,exclusion=why))
            if offer:
                append(self.out/'offers.jsonl',offer)
                accepted.append(offer)
        entry = dict(year=year,round=rnd,status='ok',master_seq=seq,index_sha256=im['sha256'],
                     response_sha256=rm['sha256'],source_url=rm['url'],observed_at=rm['observed_at'],
                     parsed_rows=len(rows),accepted=len(accepted),counts=dict(rejected),
                     archive_date_min=min(dates,default=None),archive_date_max=max(dates,default=None))
        append(self.out/'rounds.jsonl',entry)
        print(dumps(entry),flush=True)


def import_preserved(out, folder):
    if (out/'preserved-inputs.json').exists():
        return
    manifests=[]
    for stem in ['proto','proto-extra']:
        source=folder/f'{stem}-offers.json'; probes=folder/f'{stem}-probes.json'
        if not source.exists() or not probes.exists():
            continue
        raw=source.read_bytes(); ledger_raw=probes.read_bytes()
        rows=json.loads(raw); ledger=json.loads(ledger_raw)
        n=0
        for row in rows:
            if not START <= row['date'] <= END:
                continue
            q=parse_qs(urlparse(row['source_url']).query)
            if int(q['game_year'][0])!=row['raw_year'] or int(q['game_round'][0])!=row['raw_round'] or int(q['game_info_master_seq'][0])<=0:
                raise ValueError('preserved source identity mismatch')
            probe=next((p for p in ledger if p.get('requested_url',p.get('url'))==row['source_url'] and p.get('status')==200),None)
            if not probe or not re.fullmatch('[0-9a-f]{64}',probe.get('sha256','')):
                raise ValueError('missing preserved response hash')
            # Keep these quotes separate, never use them to skip complete new round acquisition.
            value=dict(row, acquisition='old_preserved',preserved_file=source.name,
                       preserved_file_sha256=digest(raw),response_sha256=probe['sha256'],
                       observed_at=probe['observed_at'],raw_master_seq=q['game_info_master_seq'][0],
                       odds_order=['home','draw','away'] if len(row['odds'])==3 else ['home','away'],
                       market='full_game_moneyline',timezone='Asia/Seoul',
                       score_source_quality='proto_archive_only_not_league_official',official_result_verified=False)
            append(out/'old-preserved-offers.jsonl',value); n+=1
        manifests.append(dict(file=source.name,sha256=digest(raw),ledger_file=probes.name,
                              ledger_sha256=digest(ledger_raw),input_rows=len(rows),in_window_rows=n))
    write_json(out/'preserved-inputs.json',manifests)


def replay(out):
    actual=read_lines(out/'offers.jsonl')
    reconstructed=[]
    reasons=Counter()
    for fact in read_lines(out/'parsed-facts.jsonl'):
        offer, reason=normalize(GameRow(**fact['row']),fact['meta'])
        assert reason==fact['exclusion'], (reason,fact['exclusion'])
        reasons[reason or 'accepted']+=1
        if offer:
            reconstructed.append(offer)
    assert reconstructed==actual, 'parsed facts do not reproduce offers exactly'
    keys=[(o['raw_year'],o['raw_round'],o['raw_game_no']) for o in actual]
    assert len(keys)==len(set(keys)), 'duplicate source quote keys'
    rounds=read_lines(out/'rounds.jsonl')
    raw_verified=0
    for r in rounds:
        if r['status']!='ok':
            continue
        assert sum(o['raw_year']==r['year'] and o['raw_round']==r['round'] for o in actual)==r['accepted']
        ix=out/'raw'/f"{r['year']}-{r['round']:03}-index.bin"
        body=out/'raw'/f"{r['year']}-{r['round']:03}-list.bin"
        if ix.exists() and body.exists():
            assert digest(ix.read_bytes())==r['index_sha256']
            assert sequence(decode(ix.read_bytes()),r['year'],r['round'])==r['master_seq']
            assert digest(body.read_bytes())==r['response_sha256']
            assert len(parse_rows(decode(body.read_bytes()),r['year'],r['round']))==r['parsed_rows']
            raw_verified+=1
    result=dict(checked_at=now(),offers=len(actual),parsed_fact_exclusions=dict(reasons),
                exact_replay=True,unique_source_keys=True,round_counts_verified=True,
                local_raw_rounds_verified=raw_verified,
                local_raw_all_available=raw_verified==sum(r['status']=='ok' for r in rounds),
                official_result_verified=False)
    write_json(out/'verification.json',result)
    print(dumps(result))


def self_test():
    from dataclasses import replace
    g=GameRow(2026,1,'001','12.31(수) 18:00','bk','KBL','hm','', '홈 80','70 원정','', [1.5,2.5],'홈승',False)
    meta=dict(source_url='fixture',response_sha256='0'*64)
    assert game_date(g).isoformat()=='2025-12-31T18:00:00+09:00'
    o,why=normalize(g,meta);assert why is None and o['home']=='홈' and o['odds']==[1.5,2.5]
    assert normalize(replace(g,is_void=True),meta)[1]=='void'
    assert normalize(replace(g,market_label='h(전반)'),meta)[1]=='not_full_moneyline'
    assert normalize(replace(g,market_tag='hp',market_label='H +1.0'),meta)[1]=='not_full_moneyline'
    assert normalize(replace(g,odds=[1.5,3.0,2.5]),meta)[1]=='not_full_moneyline'
    assert normalize(replace(g,odds=[float('nan'),2.5]),meta)[1]=='incomplete_or_invalid_vector'
    assert normalize(replace(g,home='홈 60'),meta)[1]=='score_result_disagreement'
    assert normalize(replace(g,home='홈'),meta)[1]=='missing_archive_final_score_or_result'
    assert normalize(replace(g,date_text='02.30(월) 18:00'),meta)[1]=='invalid_kickoff'
    assert normalize(replace(g,year=2025,round=120,date_text='10.27(월) 18:00'),meta)[1]=='outside_window'
    for call in ["get_gameinfo_body('proto','pt1','2026','1','','','0')", "get_gameinfo_body('proto','pt1','2025','1','','','123')"]:
        try:
            sequence(call,2026,1)
        except ValueError:
            pass
        else:
            raise AssertionError('unvalidated archive accepted')
    assert sequence("get_gameinfo_body('proto','pt1','2026','1','','','123')",2026,1)=='123'
    print('self-test: 13 date/vector/market/score/SEQ regression checks passed')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=OUT)
    p.add_argument('--collect',action='store_true')
    p.add_argument('--limit',type=int,default=162)
    p.add_argument('--replay',action='store_true')
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--preserved',type=Path)
    args=p.parse_args(); out=args.out
    out.mkdir(parents=True,exist_ok=True); (out/'raw').mkdir(exist_ok=True)
    (out/'.gitignore').write_text('raw/\nrun.log\n__pycache__/\n',encoding='utf8')
    if args.self_test:
        self_test()
    if args.preserved:
        import_preserved(out,args.preserved)
    if args.collect:
        # Chronological complete range, with a conservative earlier boundary.
        # Actual archive dates establish coverage; no date->SEQ interpolation.
        plan=[(2025,r) for r in range(120,155)]+[(2026,r) for r in range(1,128)]
        done={(r['year'],r['round']) for r in read_lines(out/'rounds.jsonl')}
        if any(r['status']!='ok' for r in read_lines(out/'rounds.jsonl')):
            raise RuntimeError('cached failure; do not automatically retry or resume host')
        c=Collector(out)
        for year,rnd in [r for r in plan if r not in done][:args.limit]:
            try:
                c.collect_round(year,rnd)
            except (requests.RequestException,ValueError,RuntimeError,UnicodeError) as exc:
                append(out/'rounds.jsonl',dict(year=year,round=rnd,status='failed',observed_at=now(),
                                             error=type(exc).__name__,message=str(exc)[:300]))
                print(f'STOP {year}:{rnd} {exc}',flush=True)
                summary(out)
                return 2
            summary(out)
    if args.replay:
        replay(out)
    result=summary(out)
    print(dumps({k:result[k] for k in ['successful_rounds','network_requests','offers','date_min','date_max']}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
