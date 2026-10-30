"""Bounded official historical baseball source repair. No outcomes or model imports."""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time as time_lib
import html as html_lib
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'experiments/source-link-baseball-20261030'
UTC = timezone.utc
JST = timezone(timedelta(hours=9))
SAFE_KEYS = ('cohort', 'event_id', 'league', 'home', 'away', 'kickoff', 'decision_at', 'available_at')


def stamp():
    return datetime.now(UTC).isoformat()


def dt(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('naive_timestamp')
    return value


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf8')


def rows_write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Fixed gzip header keeps cached replay byte-identical.
    raw = ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows).encode()
    path.write_bytes(gzip.compress(raw, mtime=0))


class Fetch:
    """Persist attempt BEFORE I/O; failures, redirects and retries consume budget."""
    def __init__(self, out=OUT, limit=160):
        self.out = Path(out)
        self.raw = self.out / 'raw'
        self.raw.mkdir(parents=True, exist_ok=True)
        self.ledger = self.out / 'http-attempts.jsonl'
        self.attempts = [json.loads(x) for x in self.ledger.read_text(encoding='utf8').splitlines()] if self.ledger.exists() else []
        self.limit = min(limit, 160)
        self.host_limit = 80
        self.session = requests.Session()
        self.session.trust_env = False  # Public requests only, never inherit credentials/proxy auth.
        self.session.headers.update({'User-Agent': 'public-historical-baseball-research/1.0'})

    def get(self, url, data=None):
        if urlparse(url).hostname not in ('npb.jp', 'www.koreabaseball.com') or not url.startswith('https://'):
            raise ValueError('nonofficial_url')
        identity = url if data is None else url + json.dumps(data, sort_keys=True)
        key = hashlib.sha256(identity.encode()).hexdigest()
        meta_path, raw_path = self.raw / (key + '.meta.json'), self.raw / (key + '.html.gz')
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding='utf8'))
            raw = gzip.decompress(raw_path.read_bytes()) if raw_path.exists() else b''
            if hashlib.sha256(raw).hexdigest() != meta['sha256']:
                raise ValueError('cache_hash_mismatch')
            return raw.decode(meta['encoding'], errors='replace'), meta
        if len(self.attempts) >= self.limit:
            raise RuntimeError('request_budget_exhausted')
        if sum(urlparse(a['url']).hostname == urlparse(url).hostname for a in self.attempts) >= self.host_limit:
            raise RuntimeError('league_request_allocation_exhausted')
        self.before_attempt()
        attempt = dict(attempt=len(self.attempts) + 1, url=url, method='GET' if data is None else 'POST', data=data, started_at=stamp())
        with self.ledger.open('a', encoding='utf8') as f:
            f.write(json.dumps(attempt) + '\n')
        self.attempts.append(attempt)
        raw = b''
        meta = dict(attempt, observed_at=None, status=None, error=None, encoding='utf-8')
        try:
            with self.session.request(attempt['method'], url, data=data, timeout=(8, 18), allow_redirects=False, stream=True) as response:
                meta['status'] = response.status_code
                chunks = []
                size = 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 3_000_000:
                        raise ValueError('response_size_limit')
                    chunks.append(chunk)
                raw = b''.join(chunks)
                # Both current official sites use UTF-8; honor an explicit legacy charset.
                charset = re.search(r'charset=["\s]*([a-zA-Z0-9_-]+)', response.headers.get('Content-Type', ''))
                meta['encoding'] = charset.group(1) if charset else 'utf-8'
                if response.status_code != 200:
                    meta['error'] = 'http_' + str(response.status_code)
        except requests.RequestException as exc:
            meta['error'] = type(exc).__name__  # Never print connection/config secret strings.
        except ValueError:
            meta['error'] = 'response_size_limit'
        meta.update(observed_at=stamp(), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), raw_path=raw_path.relative_to(self.out).as_posix())
        raw_path.write_bytes(gzip.compress(raw, mtime=0))
        dump(meta_path, meta)
        return raw.decode(meta['encoding'], errors='replace'), meta

    def before_attempt(self):
        pass

    def source_records(self):
        return [json.loads(p.read_text(encoding='utf8')) for p in sorted(self.raw.glob('*.meta.json'))]


class Phase2Fetch(Fetch):
    """Independent 200-attempt budget, immutable phase1 cache, paced new I/O."""
    def __init__(self, out=None, reuse_root=OUT):
        super().__init__(out or OUT/'phase2')
        self.limit = 200
        self.host_limit = 200
        self.reuse_root = Path(reuse_root)
        self.spacing = 0.5

    def get(self, url, data=None):
        if urlparse(url).hostname not in ('npb.jp','www.koreabaseball.com') or not url.startswith('https://'):
            raise ValueError('nonofficial_url')
        identity=url if data is None else url+json.dumps(data,sort_keys=True)
        key=hashlib.sha256(identity.encode()).hexdigest()
        path=self.reuse_root/'raw'/(key+'.meta.json')
        if path.exists():
            meta=json.loads(path.read_text(encoding='utf8'))
            raw=gzip.decompress((self.reuse_root/meta['raw_path']).read_bytes())
            if hashlib.sha256(raw).hexdigest()!=meta['sha256']:
                raise ValueError('phase1_cache_hash_mismatch')
            return raw.decode(meta['encoding'],errors='replace'),meta
        return super().get(url,data)

    def before_attempt(self):
        if self.attempts:
            elapsed=(datetime.now(UTC)-dt(self.attempts[-1]['started_at'])).total_seconds()
            if elapsed<self.spacing:
                time_lib.sleep(self.spacing-elapsed)

    def source_records(self):
        inherited=[]
        for path in sorted((self.reuse_root/'raw').glob('*.meta.json')):
            meta=json.loads(path.read_text(encoding='utf8'))
            meta.update(raw_path='../'+meta['raw_path'],cache_origin='phase1_read_only')
            inherited.append(meta)
        return inherited+super().source_records()


def freeze(out=OUT):
    out.mkdir(parents=True, exist_ok=True)
    target_path = out / 'selected-targets.jsonl.gz'
    protocol_path = out / 'protocol.json'
    if target_path.exists():
        return [json.loads(x) for x in gzip.open(target_path, 'rt', encoding='utf8')]
    case_path = ROOT / 'experiments/pregame-feature-rd-20261030/inputs/cases.jsonl.gz'
    cases = []
    with gzip.open(case_path, 'rt', encoding='utf8') as f:
        for line in f:
            row = json.loads(line)
            if row['league'] in ('KBO', 'NPB') and row['cohort'] == 'retrospective_year_strict__claimed_time_only':
                cases.append({k: row[k] for k in SAFE_KEYS})
    selected = []
    dates = {}
    for league in ('KBO', 'NPB'):
        dates[league] = sorted({dt(r['kickoff']).astimezone(JST).date().isoformat() for r in cases if r['league'] == league})[:6]
        selected.extend(r for r in cases if r['league'] == league and dt(r['kickoff']).astimezone(JST).date().isoformat() in dates[league])
    selected.sort(key=lambda r: (r['league'], r['kickoff'], r['event_id']))
    dump(protocol_path, dict(frozen_at=stamp(), input_sha256=hashlib.sha256(case_path.read_bytes()).hexdigest(),
        selection='first six distinct local target dates per league; every strict claimed-time case on those dates; no label/odds/outcome selection',
        dates=dates, primary_counts=dict(Counter(r['league'] for r in cases)), selected_counts=dict(Counter(r['league'] for r in selected)),
        max_http_attempts_including_failures=160, allocation='at most 80 attempts per league; cache reuse; no redirect/retry',
        history='target local date minus 4,3,2 days; fixed three source-calendar dates; source-calendar-end plus 24h must precede decision',
        features=['relief_pitch_count_prior3_calendar_days', 'relief_appearances_prior3_calendar_days', 'relievers_ge30_pitches_prior3_calendar_days', 'relievers_ge2_appearances_prior3_calendar_days'],
        source_timezones={'KBO':'Asia/Seoul', 'NPB':'Asia/Tokyo'}, tier='retrospective_date_lagged',
        lineups='prior nine batting starters parsed; target postgame lineups diagnostic only; no confirmed pregame XI claim',
        source_cardinality='daily schedule URL -> exact dated first-team box links -> only teams present in selected targets; all cardinalities saved before aggregation',
        target_labels_in_features=False, roi_inspected=False))
    rows_write(target_path, selected)
    return selected


KBO_TEAMS = {'KT':'KT', 'LG':'LG', 'KIA':'HT', 'SSG':'SK', 'NC':'NC', '두산':'OB', '롯데':'LT', '삼성':'SS', '키움':'WO', '한화':'HH'}
NPB_TEAMS = {'読売ジャイアンツ':'g', '阪神タイガース':'t', '横浜DeNAベイスターズ':'db', '中日ドラゴンズ':'d',
    '広島東洋カープ':'c', '東京ヤクルトスワローズ':'s', '福岡ソフトバンクホークス':'h', '北海道日本ハムファイターズ':'f',
    'オリックス・バファローズ':'b', '東北楽天ゴールデンイーグルス':'e', '埼玉西武ライオンズ':'l', '千葉ロッテマリーンズ':'m'}
KO_NPB = {'요미우리':'g', '한신':'t', '요코베이':'db', '요코하마':'db', '주니치':'d', '히로카프':'c', '히로시마':'c',
    '야쿠르트':'s', '소프트뱅':'h', '니혼햄':'f', '닛폰햄':'f', '오릭스':'b', '라쿠텐':'e', '세이부':'l', '지바롯데':'m', '지바 롯데':'m'}
ENGLISH_TEAMS = {'KBO':{'Doosan Bears':'OB','Hanwha Eagles':'HH','KIA Tigers':'HT','KT Wiz Suwon':'KT',
    'Kiwoom Heroes':'WO','LG Twins':'LG','Lotte Giants':'LT','NC Dinos':'NC','SSG Landers':'SK','Samsung Lions':'SS'},
    'NPB':{'Chiba Lotte Marines':'m','Chunichi Dragons':'d','Fukuoka S. Hawks':'h','Hanshin Tigers':'t','Hiroshima Carp':'c',
    'Nippon Ham Fighters':'f','Hokkaido Nippon-Ham Fighters':'f','Orix Buffaloes':'b','Rakuten Gold. Eagles':'e',
    'Tohoku Rakuten Golden Eagles':'e','Seibu Lions':'l','Saitama Seibu Lions':'l','Yakult Swallows':'s',
    'Tokyo Yakult Swallows':'s','Yokohama BayStars':'db','Yomiuri Giants':'g'}}


def team_code(league, name):
    if name in ENGLISH_TEAMS.get(league,{}):
        return ENGLISH_TEAMS[league][name]
    compact = re.sub(r'\s+', '', name)
    mapping = KBO_TEAMS if league == 'KBO' else KO_NPB
    matches = {code for alias, code in mapping.items() if compact.startswith(alias.replace(' ', ''))}
    if len(matches) != 1:
        raise ValueError('unknown_or_ambiguous_team:' + name)
    return matches.pop()


def clean(node):
    return re.sub(r'\s+', ' ', node.get_text(' ', strip=True)).strip() if node is not None else ''


def parse_kbo(html, expected_day):
    """Read inert XML strings only; NEVER deserialize ASP.NET/BinaryFormatter data."""
    soup = BeautifulSoup(html, 'lxml')
    state = soup.select_one('#__VIEWSTATE')
    if not state or '에러 |' in clean(soup):
        raise ValueError('kbo_summary_or_viewstate_missing')
    decoded = base64.b64decode(state['value'], validate=True).decode('utf8', errors='replace')
    identities = defaultdict(dict)
    # Only whitelisted scalar XML tags, no object instantiation or XML external entities.
    for segment in re.findall(r'<Table1\s[^>]*>(.*?)</Table1>', decoded, re.S):
        fields = {k: html_lib.unescape(v) for k, v in re.findall(r'<([A-Z_0-9]+)>([^<]*)</\1>', segment)}
        # Inning/player datasets also use Table1 but have no fixture date.
        # Never let those overwrite the dated fixture identity rows.
        if 'G_ID' not in fields or 'G_DT' not in fields:
            continue
        if fields.get('G_DT', '')[:10] != expected_day.isoformat():
            raise ValueError('kbo_response_date_mismatch')
        identities[fields['G_ID']][fields.get('TB_SC')] = fields
    games = []
    boards = soup.select('div.scoreBoard')
    for board in boards:
        table = board.select_one('table.pitchers')
        team_table = board.select_one('#tblScoreBoard1')
        if not table or not team_table:
            continue
        names = [clean(tr) for tr in team_table.select('tbody > tr')]
        if len(names) != 2 or any(x not in KBO_TEAMS for x in names):
            raise ValueError('kbo_team_heading_missing')
        away, home = (KBO_TEAMS[n] for n in names)
        candidates = [(gid, fields) for gid, fields in identities.items() if fields.get('T', {}).get('TEAM_ID') == away and fields.get('B', {}).get('TEAM_ID') == home]
        # Summary has no per-board DH id: never guess order when two matches exist.
        if len(candidates) != 1:
            raise ValueError('kbo_doubleheader_identity_ambiguous')
        gid, fields = candidates[0]
        if any(fields.get(side, {}).get('END_SC') != '1' for side in ('T', 'B')):
            continue
        pitchers = []
        for tr in table.select('tbody > tr'):
            side = 'away' if clean(tr.find('th')) == '원정' else 'home'
            code = away if side == 'away' else home
            for number, item in enumerate(tr.select('li:has(em)')):
                raw = clean(item)
                p = re.search(r'(?:,|\()P(\d+)(?:,|\))', raw)
                bf = re.search(r'\(\d+(?:\.\d+)?,(\d+),P', raw)
                pitchers.append(dict(team_id=code, player_id=clean(item.find('em')), name=clean(item.find('em')),
                    player_identity_scope='team_scoped_display_name_not_verified_registry_id',
                    role='starter' if number == 0 else 'reliever', pitch_count=int(p.group(1)) if p else None,
                    batters_faced=int(bf.group(1)) if bf else None, raw_stat_text=raw,
                    pitch_count_missing_reason=None if p else 'summary_relief_entry_only_not_pitch_count'))
        tm = re.fullmatch(r'PT(\d+)H(?:(\d+)M)?', fields['T'].get('G_TM', ''))
        start = datetime.combine(expected_day, time(int(tm[1]), int(tm[2] or 0)), JST).isoformat() if tm else None
        games.append(dict(league='KBO', game_id=gid, fact_date=expected_day.isoformat(), home_id=home, away_id=away,
            start_time=start, end_time=None, pitchers=pitchers, lineups={},
            lineup_missing_reason='official_summary_has_no_batting_order; detailed_box_endpoint_error_preserved',
            identity_method='official_summary_inert_XML_G_ID_G_DT_TEAM_ID_TB_SC_and_visible_team_headers',
            final=True))
    return games


def parse_npb(html, url, expected_day):
    soup = BeautifulSoup(html, 'lxml')
    text = clean(soup)
    date_match = re.search(r'(\d{4})年\s*(\d+)月\s*(\d+)日', text)
    if not date_match or date(*(int(x) for x in date_match.groups())) != expected_day:
        raise ValueError('npb_response_date_mismatch')
    path = urlparse(url).path
    identity = re.fullmatch(r'/scores/(\d{4})/(\d{4})/([a-z]+)-([a-z]+)-(\d+)/box.html', path)
    if not identity or identity[1] + identity[2] != expected_day.strftime('%Y%m%d'):
        raise ValueError('npb_url_date_or_fixture_mismatch')
    home, away = identity[3], identity[4]
    if '試合終了' not in text:
        raise ValueError('npb_not_final')
    pitchers, lineups = [], {}
    for side, prefix, code in (('away', 't', away), ('home', 'b', home)):
        table = soup.select_one('#tablefix_' + prefix + '_p')
        if not table:
            raise ValueError('npb_pitch_table_missing')
        heading = clean(table.find_previous(['h3','h4','h5']))
        if NPB_TEAMS.get(heading) != code:
            raise ValueError('npb_team_heading_mismatch')
        first = table.find('tr')
        headers = [clean(x) for x in first.find_all(['td','th'], recursive=False)]
        pitch_index = next((i for i, h in enumerate(headers) if h in ('球数','投球数')), None)
        if pitch_index is None or '打者' not in headers:
            raise ValueError('npb_pitch_header_missing')
        player_index = headers.index('投手')
        # Direct cell children avoid nested innings-table cells shifting the columns.
        for row in table.find_all('tr'):
            cells = row.find_all(['td','th'], recursive=False)
            if len(cells) != len(headers) or row is first:
                continue
            player = cells[player_index].find('a', href=True)
            if not player:
                continue  # Skip team total and nested inning rows.
            pid = re.search(r'/players/(\d+)\.html', player['href'])
            p, bf = clean(cells[pitch_index]), clean(cells[headers.index('打者')])
            if not pid or not p.isdigit() or not bf.isdigit():
                raise ValueError('npb_player_pitch_count_missing')
            local_number = sum(r['team_id'] == code for r in pitchers)
            pitchers.append(dict(team_id=code, player_id=pid[1], name=clean(player), role='starter' if local_number == 0 else 'reliever',
                pitch_count=int(p), batters_faced=int(bf), raw_stat_text=[clean(c) for c in cells]))
        if not any(r['team_id'] == code for r in pitchers):
            raise ValueError('npb_no_pitcher_rows')
        batting = soup.select_one('#tablefix_' + prefix + '_b')
        starters = []
        if batting:
            for row in batting.find_all('tr'):
                cells = row.find_all(['th','td'], recursive=False)
                if len(cells) < 8 or not re.fullmatch('[1-9]', clean(cells[0])):
                    continue
                link = cells[2].find('a', href=True)
                player = re.search(r'/players/(\d+)\.html', link['href']) if link else None
                starters.append(dict(order=int(clean(cells[0])), name=clean(cells[2]), player_id=player[1] if player else None, position=clean(cells[1])))
        if {s['order'] for s in starters} == set(range(1,10)) and len(starters) == 9:
            lineups[code] = starters
    started = re.search(r'◇開始\s*(\d{1,2}):(\d{2})', text)
    ended = re.search(r'◇終了\s*(\d{1,2}):(\d{2})', text)
    start = datetime.combine(expected_day, time(int(started[1]), int(started[2])), JST) if started else None
    end = datetime.combine(expected_day, time(int(ended[1]), int(ended[2])), JST) if ended else None
    if start and end and end < start:
        end += timedelta(days=1)
    return dict(league='NPB', game_id=path.removesuffix('box.html'), fact_date=expected_day.isoformat(), home_id=home, away_id=away,
        start_time=start.isoformat() if start else None, end_time=end.isoformat() if end else None,
        pitchers=pitchers, lineups=lineups, identity_method='exact_official_score_path_date_series_number_and_both_team_table_headings', final=True)


def npb_links(html, day):
    # Same exact-day first-team /scores route as src/npb_lineups.py; never farm/other dates.
    soup = BeautifulSoup(html, 'lxml')
    links = set()
    for a in soup.find_all('a', href=True):
        url = urljoin('https://npb.jp', a['href'])
        if re.fullmatch(r'https://npb.jp/scores/' + day.strftime('%Y/%m%d') + r'/[a-z]+-[a-z]+-\d+/', url):
            links.add(url + 'box.html')
    return sorted(links)


def official_fixture_id(row):
    # PR424 mixes official paths with the same path wrapped in a known provider namespace.
    # Remove only this exact namespace; retain date/teams/series-number in full.
    match=re.fullmatch(r'NPB:(/scores/\d{4}/\d{4}/[a-z]+-[a-z]+-\d+/)',row['event_id']) if row['league']=='NPB' else None
    return match[1] if match else row['event_id']


def target_match(row, game):
    if row['league'] != game['league']:
        return False, 'league_mismatch'
    day = dt(row['kickoff']).astimezone(JST).date().isoformat()
    if day != game['fact_date']:
        return False, 'date_mismatch'
    if team_code(row['league'], row['home']) != game['home_id'] or team_code(row['league'], row['away']) != game['away_id']:
        return False, 'home_away_team_mismatch'
    if row['cohort'] != 'historical_market_proxy' and official_fixture_id(row) != game['game_id']:
        return False, 'exact_official_fixture_id_mismatch'
    delta = abs((dt(row['kickoff']) - dt(game['start_time'])).total_seconds()) if game.get('start_time') else None
    if delta is None:
        if row['cohort']=='historical_market_proxy':
            return False,'auxiliary_actual_start_unverified'
        return True,'exact_official_daily_path_date_both_teams_fixture_id_start_unverified'
    if delta is not None and delta > 1800:
        return False, 'kickoff_difference_over30m'
    identity_note = 'exact_date_both_teams_unique_official_fixture' if row['cohort']=='historical_market_proxy' else 'exact_date_both_teams_official_id'
    return True, identity_note if delta == 0 else identity_note + '_actual_start_differs_within30m'


def aggregate(row, history):
    decision = dt(row.get('decision_at') or (dt(row['kickoff']) - timedelta(minutes=30)).isoformat())
    target_day = dt(row['kickoff']).astimezone(JST).date()
    days = {(target_day - timedelta(days=x)).isoformat() for x in (4,3,2)}
    games = [g for g in history if not g.get('identity_only') and g['league'] == row['league'] and g['game_id'] != official_fixture_id(row) and g['fact_date'] in days]
    values, evidence, audit = {}, {}, []
    for side in ('home','away'):
        code = team_code(row['league'], row[side])
        valid = []
        for g in games:
            if code not in (g['home_id'], g['away_id']):
                continue
            bound = dt(g['end_time']) if g['end_time'] else datetime.combine(date.fromisoformat(g['fact_date']) + timedelta(days=2), time.min, JST)
            if bound >= decision:
                audit.append(dict(side=side, game_id=g['game_id'], reason='completion_or_calendar_end_plus24h_not_before_decision'))
                continue
            valid.append(g)
        valid = list({g['game_id']:g for g in valid}.values())
        if not valid:
            audit.append(dict(side=side, reason='no_verified_prior_team_games_in_fixed_source_dates'))
            continue
        records = [dict(p, game_id=g['game_id']) for g in valid for p in g['pitchers'] if p['team_id'] == code]
        records = list({(p['game_id'],p['player_id']):p for p in records}.values())
        relief = [p for p in records if p['role'] == 'reliever']
        appearances = Counter(p['player_id'] for p in relief)
        bundle = dict(observed_relief_appearances_prior3_source_dates=len(relief),
            observed_relievers_ge2_appearances_prior3_source_dates=sum(n >= 2 for n in appearances.values()),
            observed_historical_team_games=len(valid))
        if row['league'] == 'KBO':
            starter = [p['pitch_count'] for p in records if p['role'] == 'starter']
            if starter and all(p is not None for p in starter):
                bundle['observed_starter_pitch_count_prior3_source_dates'] = sum(starter)
        elif all(p['pitch_count'] is not None for p in relief):
            pitches = Counter()
            for p in relief:
                pitches[p['player_id']] += p['pitch_count']
            bundle['observed_relief_pitch_count_prior3_source_dates'] = sum(pitches.values())
            bundle['observed_relievers_ge30_pitches_prior3_source_dates'] = sum(n >= 30 for n in pitches.values())
        for name, value in bundle.items():
            key = side + '_' + name
            values[key] = value
            meta = dict(observed_at=max(g['observed_at'] for g in valid), source_sha256=sorted({g['source_sha256'] for g in valid}),
                availability_tier='retrospective_lagged', fact_date_timezone='Asia/Seoul' if row['league']=='KBO' else 'Asia/Tokyo',
                source_game_ids=sorted(g['game_id'] for g in valid),
                note='Observed official prior-game subset on target local dates D-4,D-3,D-2. Not complete workload/availability; missing pages/dates never zero-filled. Target stats excluded.')
            if all(g['end_time'] for g in valid):
                meta['max_fact_time'] = max((g['end_time'] for g in valid), key=dt)
            else:
                meta.update(max_fact_time=None, max_fact_date=max(g['fact_date'] for g in valid), selected_fact_dates=sorted({g['fact_date'] for g in valid}),
                    calendar_bound_assumption='source calendar end plus 24h; not actual completion')
            evidence[key] = meta
    return values, evidence, audit


ARCHIVE = Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/single-vs-low-odds-parlays-20261030/experiments/single-parlay-20261030/inputs/ARCHIVE/events.jsonl.gz')


def collect(selected, out=OUT, fetch_override=None, collected_override=None):
    fetch = fetch_override or Fetch(out)
    adapter_spec=out/'source-adapter-spec.json'
    if not adapter_spec.exists():
        dump(adapter_spec,dict(frozen_at=stamp(),frozen_before_parent_evaluation=True,
            reason='Source inspection resolves available scalar fields; original selection protocol remains intact. Planned KBO reliever pitch counts unavailable, so distinct starter-pitch/relief-appearance bundle is declared.',
            KBO_features=['observed_starter_pitch_count_prior3_source_dates','observed_relief_appearances_prior3_source_dates','observed_relievers_ge2_appearances_prior3_source_dates','observed_historical_team_games'],
            NPB_features=['observed_relief_pitch_count_prior3_source_dates','observed_relief_appearances_prior3_source_dates','observed_relievers_ge30_pitches_prior3_source_dates','observed_relievers_ge2_appearances_prior3_source_dates','observed_historical_team_games'],
            window='D-4,D-3,D-2 local calendar dates; observed games only, no empty-window zeros',
            identity='exact league/date/home/away; primary exact official id; auxiliary unique fixture plus actual start within30m',
            clocks='NPB official actual end HH:MM on verified source date (next-day if end<start). KBO calendar date only + end-of-day +24h.',
            fact_date_timezone={'KBO':'Asia/Seoul','NPB':'Asia/Tokyo'},roi_inspected=False,model_fitted=False))
    parsed, schedules, errors = collected_override if collected_override is not None else ([], [], [])
    # Source cardinality is persisted incrementally BEFORE feature construction.
    for league in (() if collected_override is not None else ('KBO','NPB')):
        targets = [r for r in selected if r['league']==league]
        relevant = {team_code(league, r[s]) for r in targets for s in ('home','away')}
        target_dates = {dt(r['kickoff']).astimezone(JST).date() for r in targets}
        prior_dates = {day - timedelta(days=x) for day in target_dates for x in (4,3,2)}
        # Primary target-date identity first; also supplies prior stats for other target dates.
        dates = sorted(target_dates) + sorted(prior_dates - target_dates)
        for day in dates:
            try:
                if league == 'KBO':
                    url = 'https://www.koreabaseball.com/Game/SummaryView.aspx'
                    html, meta = fetch.get(url, dict(gamedate=day.strftime('%Y%m%d'), leagueId='1', seriesId='0'))
                    if meta['error']:
                        raise ValueError(meta['error'])
                    games = parse_kbo(html, day)
                    schedules.append(dict(league=league,date=day.isoformat(),url=url,request_data=meta['data'],source_sha256=meta['sha256'],
                        raw_games=len(games), selected_games=len(games), game_ids=[g['game_id'] for g in games],
                        empty_reason=None if games else 'no_completed_regular_game_rows_in_summary'))
                    for g in games:
                        g.update(source_url=url, source_sha256=meta['sha256'], observed_at=meta['observed_at'])
                    parsed.extend(games)
                else:
                    url = f'https://npb.jp/bis/{day.year}/games/gm{day:%Y%m%d}.html'
                    html, meta = fetch.get(url)
                    if meta['error']:
                        raise ValueError(meta['error'])
                    links = npb_links(html, day)
                    chosen = [u for u in links if set(urlparse(u).path.split('/')[-2].split('-')[:2]) & relevant]
                    schedules.append(dict(league=league,date=day.isoformat(),url=url,source_sha256=meta['sha256'],raw_box_urls=links,
                        selected_box_urls=chosen,raw_games=len(links),selected_games=len(chosen),empty_reason=None if links else 'no_exact_date_first_team_box_links'))
                    dump(out/'source-cardinality.json', schedules)
                    for box_url in chosen:
                        try:
                            box, bm = fetch.get(box_url)
                            if bm['error']:
                                raise ValueError(bm['error'])
                            g = parse_npb(box, box_url, day)
                            g.update(source_url=box_url, source_sha256=bm['sha256'], observed_at=bm['observed_at'])
                            parsed.append(g)
                        except (ValueError,RuntimeError) as exc:
                            errors.append(dict(league=league, date=day.isoformat(),url=box_url,reason=str(exc)))
            except (ValueError, RuntimeError) as exc:
                errors.append(dict(league=league, date=day.isoformat(),reason=str(exc)))
            dump(out/'source-cardinality.json', schedules)
    parsed = sorted({(g['league'],g['game_id']):g for g in parsed}.values(),key=lambda g:(g['league'],g['game_id']))
    rows_write(out/'parsed-official-games.jsonl.gz', [g for g in parsed if not g.get('identity_only')])
    if collected_override is not None:
        rows_write(out/'target-identities.jsonl.gz',[g for g in parsed if g.get('identity_only')])
    matches, diagnostics, features, audit = [], [], [], []
    def attach(row, auxiliary=False):
        candidates = [g for g in parsed if g['league']==row['league'] and g['fact_date']==dt(row['kickoff']).astimezone(JST).date().isoformat()
                      and g['home_id']==team_code(row['league'],row['home']) and g['away_id']==team_code(row['league'],row['away'])]
        verified = [(g,target_match(row,g)) for g in candidates]
        accepted = [g for g,(ok,reason) in verified if ok]
        if len(accepted)!=1:
            reason = 'ambiguous_doubleheader_or_duplicate' if len(accepted)>1 else ';'.join(reason for _,(_,reason) in verified) or 'official_target_fixture_not_collected'
            matches.append(dict(**row,matched=False,reason=reason,auxiliary=auxiliary))
            return
        game=accepted[0]
        _,reason=target_match(row,game)
        matches.append(dict(**row,matched=True,reason=reason,official_game_id=game['game_id'],auxiliary=auxiliary,
            source_sha256=game['source_sha256'], actual_start=game['start_time'],
            identity_normalization='known_NPB_provider_namespace_only' if official_fixture_id(row)!=row['event_id'] else 'none'))
        diagnostics.append(dict(event_id=row['event_id'], official_game_id=game['game_id'],lineups=game['lineups'],source_sha256=game['source_sha256'],
            reason='postgame_target_XI_publication_time_unproven_DIAGNOSTIC_ONLY',excluded_from_features=True,auxiliary=auxiliary))
        values,evidence,failures=aggregate(row,parsed)
        audit.extend(dict(event_id=row['event_id'],**f) for f in failures)
        if values:
            features.append(dict(cohort=row['cohort'],event_id=row['event_id'],league=row['league'],kickoff=row['kickoff'],
                decision_at=row.get('decision_at') or (dt(row['kickoff'])-timedelta(minutes=30)).isoformat(),features=values,feature_evidence=evidence,
                source_ids=sorted({h for e in evidence.values() for h in e['source_sha256']}),auxiliary=auxiliary,
                feature_bundle='kbo-official-observed-starter-pitches-relief-appearances-v1' if row['league']=='KBO' else 'npb-official-observed-relief-pitches-appearances-v1'))
    for row in selected:
        attach(row)
    aux_inputs=[]
    dates_by_league={league:{g['fact_date'] for g in parsed if g['league']==league} for league in ('KBO','NPB')}
    # This additional cohort selection is fixed before auxiliary attachment and any parent evaluation.
    aux_protocol = out/'auxiliary-protocol.json'
    if not aux_protocol.exists():
        dump(aux_protocol,dict(frozen_at=stamp(), source_archive=str(ARCHIVE),
            selection='historical_market_proxy KBO/NPB rows on already fetched official dates; exact local date, home, away, unique fixture and <=30m actual start difference',
            extra_http_requests=0, label_fields_accessed=False, model_fitted=False, roi_inspected=False,
            parent_requirement='Parent canonicalizes fullgame cases and excludes auxiliary dates shared with primary before evaluation.'))
    if ARCHIVE.exists():
        with gzip.open(ARCHIVE,'rt',encoding='utf8') as f:
            for line in f:
                r=json.loads(line)
                if r['league'] not in dates_by_league or r['cohort']!='historical_market_proxy' or dt(r['kickoff']).astimezone(JST).date().isoformat() not in dates_by_league[r['league']]:
                    continue
                safe={k:r[k] for k in SAFE_KEYS if k in r}
                safe['decision_at']=(dt(r['kickoff'])-timedelta(minutes=30)).isoformat()
                aux_inputs.append(safe)
        for row in aux_inputs:
            attach(row,True)
    primary=[r for r in features if not r['auxiliary']]
    auxiliary=[r for r in features if r['auxiliary']]
    rows_write(out/'features.jsonl.gz', primary)
    rows_write(out/'auxiliary-features.jsonl.gz', auxiliary)
    rows_write(out/'auxiliary-selected-inputs.jsonl.gz', aux_inputs)
    rows_write(out/'fixture-joins.jsonl.gz',matches)
    rows_write(out/'target-lineups-DIAGNOSTIC-ONLY.jsonl.gz',diagnostics)
    rows_write(out/'field-audit.jsonl.gz',audit)
    manifest=fetch.source_records()
    for meta in manifest:
        html=gzip.decompress((out/meta['raw_path']).read_bytes()).decode(meta['encoding'],errors='replace')
        if '에러 | KBO홈페이지' in html:
            meta['semantic_error']='official_http200_error_page'
            errors.append(dict(league='KBO',url=meta['url'],request_data=meta.get('data'),reason=meta['semantic_error'],source_sha256=meta['sha256']))
    rows_write(out/'negative-probes.jsonl.gz', errors)
    dump(out/'source-manifest.json',dict(sources=manifest,total_http_attempts=len(fetch.attempts),
        archive_input_path=str(ARCHIVE),archive_sha256=hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() if ARCHIVE.exists() else None))
    joined={r['event_id']:r for r in matches if not r['auxiliary']}
    by_event={r['event_id']:r for r in primary}
    coverage=[]
    with gzip.open(ROOT/'experiments/pregame-feature-rd-20261030/inputs/cases.jsonl.gz','rt',encoding='utf8') as f:
        for line in f:
            r=json.loads(line)
            if r['league'] not in ('KBO','NPB') or r['cohort']!='retrospective_year_strict__claimed_time_only':
                continue
            event=r['event_id'];matched=joined.get(event,{}).get('matched',False)
            outside='outside_frozen_first30_target_dates' if isinstance(fetch,Phase2Fetch) else 'outside_frozen_first6_target_dates'
            reason=outside if event not in joined else joined[event]['reason'] if not matched else 'no_verified_prior_history' if event not in by_event else 'features_available'
            coverage.append(dict(cohort=r['cohort'],event_id=event,league=r['league'],selected=event in joined,matched=matched,feature_row=event in by_event,reason=reason))
    rows_write(out/'primary-coverage.jsonl.gz',coverage)
    availability=[]
    for r in selected:
        availability.append(dict(event_id=r['event_id'],league=r['league'],available_feature_fields=sorted(by_event.get(r['event_id'],{}).get('features',{})),
            target_confirmed_lineup=dict(available=False,reason='no_pregame_publication_or_capture_evidence'),
            relief_pitch_count=dict(available=r['league']=='NPB' and r['event_id'] in by_event,reason='official_pitch_counts' if r['league']=='NPB' else 'summary_relief_pitch_count_missing'),
            batting_lineup=dict(available=r['league']=='NPB',role='postgame_diagnostic_only' if r['league']=='NPB' else 'missing_official_batting_order')))
    rows_write(out/'field-availability.jsonl.gz',availability)
    summary=dict(http_attempts=len(fetch.attempts),selected_primary=dict(Counter(r['league'] for r in selected)),
        parsed_games=dict(Counter(g['league'] for g in parsed if not g.get('identity_only'))), parsed_pitcher_rows=dict(Counter(g['league'] for g in parsed for _ in g['pitchers'])),
        parsed_pitch_counts=dict(Counter(g['league'] for g in parsed for p in g['pitchers'] if p['pitch_count'] is not None)),
        parsed_nine_player_lineups=dict(Counter(g['league'] for g in parsed for x in g['lineups'].values() if len(x)==9)),
        matched_primary=dict(Counter(r['league'] for r in matches if r['matched'] and not r['auxiliary'])),
        primary_feature_rows=dict(Counter(r['league'] for r in primary)),auxiliary_feature_rows=dict(Counter(r['league'] for r in auxiliary)),
        matched_auxiliary=dict(Counter(r['league'] for r in matches if r['matched'] and r['auxiliary'])),
        primary_coverage_reasons=dict(Counter(r['reason'] for r in coverage)),auxiliary_candidates=dict(Counter(r['league'] for r in aux_inputs)),
        unmatched_reasons=dict(Counter(r['reason'] for r in matches if not r['matched'])),negative_probe_reasons=dict(Counter(r['reason'] for r in errors)),
        field_limitations={'KBO_relief_pitch_count':'unavailable in Summary; never substitute BF or entry inning',
            'KBO_batting_order':'unavailable in Summary; detailed official endpoint returned error page',
            'KBO_player_identity':'team-scoped display names only; repeat-appearance counts can collide for homonyms',
            'all_target_lineups':'diagnostic_only_unproven_pregame_publication',
            'workload':'observed subset, not complete window; empty/missing source dates not zero-filled'},roi_inspected=False,model_fitted=False)
    if isinstance(fetch,Phase2Fetch):
        summary.update(original_phase1_http_attempts=95,additional_phase2_http_attempts=len(fetch.attempts),combined_http_attempts=95+len(fetch.attempts),
            identity_only_primary_targets=sum(g.get('identity_only',False) for g in parsed),
            new_attempts_by_host=dict(Counter(urlparse(a['url']).hostname for a in fetch.attempts)))
    dump(out/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False))


def phase1_unchanged(out):
    baseline=json.loads((out/'phase1-baseline-hashes.json').read_text(encoding='utf8'))
    for name,digest in baseline.items():
        assert hashlib.sha256((OUT/name).read_bytes()).hexdigest()==digest,('phase1_artifact_changed',name)
    return len(baseline)


def structural_support(out):
    """Count fixtures/dates only. Does not inspect labels, prices or fit models."""
    primary=list(map(json.loads,gzip.open(out/'features.jsonl.gz','rt',encoding='utf8')))
    auxiliary=list(map(json.loads,gzip.open(out/'auxiliary-features.jsonl.gz','rt',encoding='utf8')))
    results=[]
    for league in ('KBO','NPB'):
        rows=[r for r in primary if r['league']==league]
        day=lambda r:dt(r['kickoff']).astimezone(JST).date().isoformat()
        dates=sorted({day(r) for r in rows})
        for train_fraction,val_fraction in ((.6,.2),(.5,.2)):
            a=max(1,int(len(dates)*train_fraction));b=max(a+1,int(len(dates)*(train_fraction+val_fraction)))
            if b>=len(dates):
                continue
            validation_start=min(dt(r['decision_at']) for r in rows if day(r) in dates[a:b])
            primary_train={r['event_id'] for r in rows if day(r) in dates[:a] and dt(r['kickoff'])<validation_start-timedelta(hours=48)}
            aux_train={r['event_id'] for r in auxiliary if r['league']==league and day(r) not in dates and dt(r['kickoff'])<validation_start-timedelta(hours=48)}
            test={r['event_id'] for r in rows if day(r) in dates[b:]}
            results.append(dict(league=league,split='60-20-20' if train_fraction==.6 else '50-20-30',primary_feature_dates=len(dates),
                primary_train_candidate_fixtures_after48h=len(primary_train),auxiliary_train_candidate_fixtures_after48h_excluding_primary_dates=len(aux_train),
                test_candidate_fixtures=len(test),primary_train_ge30=len(primary_train)>=30,test_ge10=len(test)>=10))
    result=dict(scope='Structural fixture counts only. Parent must check labels, canonical fullgame eligibility, actual price/quote clocks and common matched support. No outcome or ROI access.',
        counts=results,models_fitted=False,ROI_inspected=False)
    dump(out/'structural-support-counts.json',result)
    return result


def collect_phase2(out=None):
    out=Path(out or OUT/'phase2')
    phase1_unchanged(out)
    protocol=json.loads((out/'protocol.json').read_text(encoding='utf8'))
    assert protocol['max_additional_http_attempts']==200 and protocol['frozen_before_fetch']
    # The user requested a separate protocol commit before any phase2 HTTP.
    protocol_relative=(out/'protocol.json').relative_to(ROOT).as_posix()
    freeze_commit=subprocess.check_output(['git','log','-1','--format=%H','--',protocol_relative],cwd=ROOT,text=True).strip()
    assert freeze_commit,'phase2_protocol_not_committed_before_collection'
    frozen=subprocess.check_output(['git','show',freeze_commit+':'+protocol_relative],cwd=ROOT)
    assert json.loads(frozen)==protocol,'phase2_protocol_changed_after_freeze_commit'
    selected=[json.loads(line) for line in gzip.open(out/'selected-targets.jsonl.gz','rt',encoding='utf8')]
    # Validate every frozen alias before any new HTTP; matching still verifies both official team codes.
    for row in selected:
        for side in ('home','away'):
            team_code(row['league'],row[side])
    fetch=Phase2Fetch(out)
    actual={(g['league'],g['game_id']):g for g in map(json.loads,gzip.open(OUT/'parsed-official-games.jsonl.gz','rt',encoding='utf8'))}
    identities={}
    schedules=[]
    errors=[]
    kbo=[r for r in selected if r['league']=='KBO']
    target_days={dt(r['kickoff']).astimezone(JST).date() for r in kbo}
    all_days=target_days|{d-timedelta(days=x) for d in target_days for x in (4,3,2)}
    for day in sorted(all_days):
        url='https://www.koreabaseball.com/Game/SummaryView.aspx'
        try:
            html,meta=fetch.get(url,dict(gamedate=day.strftime('%Y%m%d'),leagueId='1',seriesId='0'))
            if meta['error']:
                raise ValueError(meta['error'])
            games=parse_kbo(html,day)
            schedules.append(dict(league='KBO',date=day.isoformat(),url=url,request_data=meta['data'],source_sha256=meta['sha256'],
                raw_games=len(games),selected_games=len(games),game_ids=[g['game_id'] for g in games],
                empty_reason=None if games else 'no_completed_regular_game_rows_in_summary'))
            for game in games:
                game.update(source_url=url,source_sha256=meta['sha256'],observed_at=meta['observed_at'])
                actual[('KBO',game['game_id'])]=game
        except (ValueError,RuntimeError) as exc:
            errors.append(dict(league='KBO',date=day.isoformat(),url=url,reason=str(exc)))
        dump(out/'source-cardinality.json',schedules)
    npb=[r for r in selected if r['league']=='NPB']
    def daily(day,role):
        url=f'https://npb.jp/bis/{day.year}/games/gm{day:%Y%m%d}.html'
        html,meta=fetch.get(url)
        if meta['error']:
            raise ValueError(meta['error'])
        match=re.search(r'(\d{4})年\s*(\d+)月\s*(\d+)日',clean(BeautifulSoup(html,'lxml')))
        if not match or date(*(int(x) for x in match.groups()))!=day:
            raise ValueError('npb_daily_response_date_mismatch')
        links=npb_links(html,day)
        record=dict(league='NPB',date=day.isoformat(),url=url,role=role,source_sha256=meta['sha256'],
            raw_box_urls=links,selected_box_urls=[],raw_games=len(links),selected_games=0,
            empty_reason=None if links else 'no_exact_date_first_team_box_links')
        schedules.append(record)
        return links,meta,record
    for target_day in sorted({dt(r['kickoff']).astimezone(JST).date() for r in npb}):
        cases=[r for r in npb if dt(r['kickoff']).astimezone(JST).date()==target_day]
        teams={team_code('NPB',r[side]) for r in cases for side in ('home','away')}
        try:
            links,meta,record=daily(target_day,'target_identity_only')
            paths={urlparse(url).path.removesuffix('box.html'):url for url in links}
            record['selected_identity_paths']=sorted(official_fixture_id(r) for r in cases if official_fixture_id(r) in paths)
            for row in cases:
                event=official_fixture_id(row)
                if event not in paths:
                    errors.append(dict(league='NPB',date=target_day.isoformat(),event_id=event,reason='exact_target_path_absent_from_official_daily_index'))
                    continue
                code=event.strip('/').split('/')[-1].split('-')
                identities[('NPB',event)]=dict(league='NPB',game_id=event,fact_date=target_day.isoformat(),home_id=code[0],away_id=code[1],
                    start_time=None,end_time=None,pitchers=[],lineups={},final=False,identity_only=True,
                    identity_method='official_daily_exact_fixture_path_date_home_away; actual_start_unverified; no_target_stats_requested',
                    source_url=meta['url'],source_sha256=meta['sha256'],observed_at=meta['observed_at'])
        except (ValueError,RuntimeError) as exc:
            errors.append(dict(league='NPB',date=target_day.isoformat(),role='target_identity_only',reason=str(exc)))
        dump(out/'source-cardinality.json',schedules)
        for offset in (4,3,2):
            day=target_day-timedelta(days=offset)
            try:
                links,meta,record=daily(day,'prior_pitching_for_'+target_day.isoformat())
                chosen=[url for url in links if set(urlparse(url).path.split('/')[-2].split('-')[:2])&teams]
                record.update(selected_box_urls=chosen,selected_games=len(chosen))
                dump(out/'source-cardinality.json',schedules)
                for url in chosen:
                    event=urlparse(url).path.removesuffix('box.html')
                    if ('NPB',event) in actual:
                        continue
                    try:
                        html,bm=fetch.get(url)
                        if bm['error']:
                            raise ValueError(bm['error'])
                        game=parse_npb(html,url,day)
                        game.update(source_url=url,source_sha256=bm['sha256'],observed_at=bm['observed_at'])
                        actual[('NPB',game['game_id'])]=game
                    except (ValueError,RuntimeError) as exc:
                        errors.append(dict(league='NPB',date=day.isoformat(),target_date=target_day.isoformat(),url=url,reason=str(exc)))
            except (ValueError,RuntimeError) as exc:
                errors.append(dict(league='NPB',date=day.isoformat(),target_date=target_day.isoformat(),reason=str(exc)))
        dump(out/'collection-progress.json',dict(additional_attempts=len(fetch.attempts),max_additional_attempts=200,
            last_np_target_date=target_day.isoformat(),actual_games=dict(Counter(k[0] for k in actual))))
    identities={k:g for k,g in identities.items() if k not in actual}
    parsed=list(actual.values())+list(identities.values())
    # Materialize with the same feature names, aggregation and field evidence as phase1.
    collect(selected,out,fetch_override=fetch,collected_override=(parsed,schedules,errors))
    attempts=fetch.attempts
    gaps=[(dt(b['started_at'])-dt(a['started_at'])).total_seconds() for a,b in zip(attempts,attempts[1:])]
    assert not gaps or min(gaps)>=0.499,'request_spacing_below_0.5s'
    dump(out/'request-accounting.json',dict(original_phase1_attempts=95,additional_phase2_attempts=len(attempts),
        combined_attempts=95+len(attempts),additional_cap=200,minimum_additional_start_spacing_seconds=min(gaps) if gaps else None,
        new_attempts_by_host=dict(Counter(urlparse(a['url']).hostname for a in attempts)),
        cached_original_responses_available=95,phase1_unchanged_files=phase1_unchanged(out)))
    structural_support(out)


def verify(out=OUT):
    """Offline replay and field-clock verification only; no fitting or evaluation."""
    from rd_asof_evidence import feature_gate
    manifest=json.loads((out/'source-manifest.json').read_text(encoding='utf8'))
    sources=manifest['sources']
    for meta in sources:
        raw=gzip.decompress((out/meta['raw_path']).read_bytes())
        assert hashlib.sha256(raw).hexdigest()==meta['sha256']
        assert dt(meta['started_at'])<=dt(meta['observed_at'])
    count=len((out/'http-attempts.jsonl').read_text(encoding='utf8').splitlines())
    assert count==manifest['total_http_attempts'] and count<=(200 if out.name=='phase2' else 160)
    excluded={'validation.json','artifact-hashes.json','changed-paths.txt'}
    def fingerprint():
        return {p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                for p in out.rglob('*') if p.is_file() and p.name not in excluded}
    before=fingerprint()
    original=requests.Session.request
    def blocked(*args,**kwargs):
        raise AssertionError('offline_replay_attempted_network')
    requests.Session.request=blocked
    try:
        if out.name=='phase2':
            collect_phase2(out)
        else:
            collect(freeze(out),out)
    finally:
        requests.Session.request=original
    assert before==fingerprint(),'cached_replay_changed_artifacts'
    rows=[json.loads(line) for filename in ('features.jsonl.gz','auxiliary-features.jsonl.gz')
          for line in gzip.open(out/filename,'rt',encoding='utf8')]
    games={g['game_id']:g for g in map(json.loads,gzip.open(out/'parsed-official-games.jsonl.gz','rt',encoding='utf8'))}
    gates=Counter()
    hashes={m['sha256'] for m in sources}
    for row in rows:
        assert not set(('outcome','odds','market_p','choices','lineups','external_p'))&set(row)
        target_day=dt(row['kickoff']).astimezone(JST).date()
        for name,value in row['features'].items():
            e=row['feature_evidence'][name]
            assert set(e['source_sha256'])<=hashes
            assert official_fixture_id(row) not in e['source_game_ids']
            for source_game in e['source_game_ids']:
                game=games[source_game]
                assert not game.get('identity_only') and game['league']==row['league']
                assert game['fact_date'] in {(target_day-timedelta(days=x)).isoformat() for x in (4,3,2)}
            if e.get('max_fact_date'):
                assert date.fromisoformat(e['max_fact_date'])<=target_day-timedelta(days=2)
            ok,reason=feature_gate(value,e,row['decision_at'],view='retrospective_date_lagged')
            assert ok,(row['event_id'],name,reason)
            gates[reason]+=1
    regression=subprocess.run([sys.executable,'-m','pytest','tests/test_rd_kbo_npb_sources.py','tests/test_npb_lineups.py',
        '-q','--disable-warnings'],cwd=ROOT,capture_output=True,text=True,timeout=60)
    assert regression.returncode==0,regression.stdout[-2000:]+regression.stderr[-1000:]
    result=dict(valid=True,verified_at=stamp(),network_disabled_replay_byte_identical=True,
        total_http_attempts=count,raw_response_hashes_verified=len(sources),feature_rows=len(rows),
        feature_fields=sum(gates.values()),field_gate_reasons=dict(gates),
        regression_command='python -m pytest tests/test_rd_kbo_npb_sources.py tests/test_npb_lineups.py -q --disable-warnings',
        observed_regression_result=regression.stdout.strip().splitlines()[-1],roi_inspected=False,models_fitted=False)
    dump(out/'validation.json',result)
    tracked=['scripts/rd_kbo_npb_sources.py','tests/test_rd_kbo_npb_sources.py','docs/research/2026-10-30-source-link-baseball.md']
    paths=tracked+[p.relative_to(ROOT).as_posix() for p in out.rglob('*') if p.is_file() and p.name!='changed-paths.txt']
    for filename in ('changed-paths.txt','artifact-hashes.json'):
        path=(out/filename).relative_to(ROOT).as_posix()
        if path not in paths:
            paths.append(path)
    (out/'changed-paths.txt').write_text('\n'.join(sorted(paths))+'\n',encoding='utf8')
    dump(out/'artifact-hashes.json',{p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted(out.rglob('*')) if p.is_file() and p.name!='artifact-hashes.json'})
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['freeze', 'probe', 'collect','verify','collect-phase2','verify-phase2'])
    args = parser.parse_args()
    if args.command=='collect-phase2':
        collect_phase2()
        return
    if args.command=='verify-phase2':
        verify(OUT/'phase2')
        return
    selected = freeze()
    if args.command == 'freeze':
        print(json.dumps(dict(selected=Counter(r['league'] for r in selected))))
    elif args.command == 'probe':
        fetch = Fetch()
        for url in ('https://www.koreabaseball.com/Game/Summary.aspx?gameId=20260328KTLG0&seasonId=2026',
                    'https://npb.jp/bis/2026/games/gm20260329.html', 'https://npb.jp/bis/2026/games/'):
            html, meta = fetch.get(url)
            soup = BeautifulSoup(html, 'lxml')
            print(json.dumps(dict(url=url, status=meta['status'], error=meta['error'], title=soup.title.get_text() if soup.title else None,
                tables=[{'id':t.get('id'), 'class':t.get('class'), 'rows':[[c.get_text(' ',strip=True) for c in tr.find_all(['th','td'])] for tr in t.find_all('tr')[:3]]} for t in soup.find_all('table')][:12],
                links=[a['href'] for a in soup.find_all('a',href=True) if re.search('box|Summary|bis/2026/games',a['href'])][:12]), ensure_ascii=False))
    elif args.command == 'collect':
        collect(selected)
    elif args.command == 'verify':
        verify()


if __name__ == '__main__':
    main()
