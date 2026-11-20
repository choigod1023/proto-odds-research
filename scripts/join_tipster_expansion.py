"""Local exact-alias join for the expansion audit. Never matches by final score."""
from collections import defaultdict, Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from scripts.evaluate_tipster_expansion import team_key

LEAGUES = {'K1':'K리그1', 'K2':'K리그2', 'K League 1':'K리그1', 'K League 2':'K리그2'}
ALIASES = {
 'K리그1': {'울산':['울산HD','울산현대','울산 현대','Ulsan HD'], '포항':['포항스틸러스'],
           '광주':['광주FC'], '대구':['대구FC'], '전북':['전북현대'], '강원':['강원FC'],
           '서울':['FC서울'], '제주':['제주유나이티드','제주SK'], '인천':['인천유나이티드'],
           '수원FC':['수원 FC'], '대전':['대전하나시티즌'], '김천':['김천상무'],
           '안양':['FC안양']},
 'K리그2': {'수원삼성':['수원삼성블루윙즈'], '김포':['김포FC'], '부천':['부천FC','부천FC1995'],
           '부산':['부산아이파크'], '성남':['성남FC'], '전남':['전남드래곤즈'], '경남':['경남FC'],
           '충북청주':['충북청주FC'], '천안':['천안시티','천안시티FC'], '화성':['화성FC']},
 'KBO': {'LG':['LG트윈스'], '두산':['두산베어스'], '삼성':['삼성라이온즈','삼성라이온스'],
         '롯데':['롯데자이언츠'], 'KIA':['기아','KIA타이거즈'], 'NC':['NC다이노스'],
         'KT':['KT위즈'], '한화':['한화이글스'], 'SSG':['SSG랜더스'], '키움':['키움히어로즈']},
 'NPB': {'요미우리':['巨人','読売','読売ジャイアンツ','Yomiuri Giants','요미우리자이언츠'],
         '요코하마':['DeNA','横浜DeNA','横浜DeNAベイスターズ','요코하마DeNA','요코하마DeNA베이스타스','Yokohama DeNA BayStars','Yokohama BayStars'],
         '한신':['阪神','阪神タイガース','한신타이거즈','한신타이거스','Hanshin Tigers'],
         '야쿠르트':['ヤクルト','東京ヤクルト','東京ヤクルトスワローズ','야쿠르트스왈로즈','야쿠르트스왈로스','Tokyo Yakult Swallows'],
         '히로시마':['広島','広島東洋','広島東洋カープ','히로시마도요카프','히로시마카프','Hiroshima Toyo Carp','Hiroshima Carp'],
         '주니치':['中日','中日ドラゴンズ','주니치드래곤즈','Chunichi Dragons'],
         '소프트뱅크':['ソフトバンク','福岡ソフトバンク','福岡ソフトバンクホークス','소프트뱅크호크스','Fukuoka SoftBank Hawks','SoftBank Hawks'],
         '닛폰햄':['日本ハム','北海道日本ハム','北海道日本ハムファイターズ','니혼햄','니혼햄파이터스','Nippon-Ham Fighters','Hokkaido Nippon-Ham Fighters'],
         '라쿠텐':['楽天','東北楽天','東北楽天ゴールデンイーグルス','라쿠텐골든이글스','Rakuten Golden Eagles','Rakuten Eagles','Tohoku Rakuten Golden Eagles'],
         '오릭스':['オリックス','オリックス・バファローズ','오릭스버팔로스','Orix Buffaloes'],
         '세이부':['西武','埼玉西武','埼玉西武ライオンズ','세이부라이온스','Saitama Seibu Lions','Seibu Lions'],
         '지바롯데':['ロッテ','千葉ロッテ','千葉ロッテマリーンズ','지바롯데마린스','Chiba Lotte Marines','Lotte Marines']},
 'KBL': {'고양소노':['고양 소노 스카이거너스','Goyang Sono','Goyang Sono Skygunners'],
         '안양정관':['안양 정관장 레드부스터스','안양정관장','Anyang JungKwanJang'],
         'KT소닉붐':['수원 KT 소닉붐','Suwon KT','Suwon KT Sonicboom'],
         '부산KCC':['부산 KCC 이지스','Busan KCC Egis','Busan KCC','KCC Egis'],
         '창원LG':['창원 LG 세이커스','Changwon LG','Changwon LG Sakers','LG Sakers'],
         '서울SK':['서울 SK 나이츠','Seoul SK','Seoul SK Knights','Seoul Knights'],
         '서울삼성':['서울 삼성 썬더스','Seoul Samsung','Seoul Samsung Thunders'],
         '원주DB':['원주 DB 프로미','Wonju DB','Wonju DB Promy'],
         '울산모비':['울산 현대모비스 피버스','Ulsan Hyundai Mobis','Ulsan Mobis Phoebus','Ulsan Hyundai Mobis Phoebus','Mobis Phoebus'],
         '한국가스':['대구 한국가스공사 페가수스','Daegu KOGAS','Daegu Kogas Pegasus','Daegu KOGAS Pegasus','KoGas']},
 'NBA': {'Los Angeles Lakers':['LA Lakers','Lakers'], 'Los Angeles Clippers':['LA Clippers','Clippers'],
         'Portland Trail Blazers':['Portland','Trail Blazers'], 'Philadelphia 76ers':['Philadelphia','76ers'],
         'Indiana Pacers':['Indiana','Pacers'], 'Sacramento Kings':['Sacramento','Kings'],
         'San Antonio Spurs':['San Antonio','Spurs'], 'New York Knicks':['New York','Knicks'],
         'Boston Celtics':['Boston','Celtics'], 'Brooklyn Nets':['Brooklyn','Nets']},
 'MLB': {'New York Yankees':['Yankees','뉴욕양키'], 'Minnesota Twins':['Twins','미네트윈'],
         'Milwaukee Brewers':['Brewers','밀워브루'], 'Tampa Bay Rays':['Rays','탬파레이'],
         'San Francisco Giants':['Giants','샌프자이'], 'Arizona Diamondbacks':['Diamondbacks','애리다이'],
         'Philadelphia Phillies':['Phillies','필라필리'], 'Los Angeles Dodgers':['Dodgers','LA다저스'],
         'Texas Rangers':['Rangers','텍사레인'], 'Houston Astros':['Astros','휴스애스'],
         'Chicago Cubs':['Cubs','시카컵스'], 'Pittsburgh Pirates':['Pirates','피츠파이'],
         'Boston Red Sox':['Red Sox','보스레드'], 'Cincinnati Reds':['Reds','신시레즈'],
         'Chicago White Sox':['White Sox','시카화이'], 'Cleveland Guardians':['Guardians','클리가디']},
}


def canonical(name, league):
    key = team_key(name)
    for target, names in ALIASES.get(league, {}).items():
        if key in {team_key(x) for x in [target,*names]}:
            return team_key(target)
    return key


def read_rows(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    if text.lstrip().startswith('['):
        return json.loads(text)
    obj = json.loads(text) if text.lstrip().startswith('{') and '\n{' not in text else None
    if obj is not None:
        if 'ledger' in obj: return obj['ledger']
        raise ValueError('expected JSON array/JSONL/ledger: '+str(path))
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def official_url(url):
    return urlsplit(url or '').hostname in {'npb.jp','api.kbl.or.kr','statsapi.mlb.com',
            'cdn.nba.com','api.kleague.com','portal.kleague.com','data.kleague.com',
            'www.koreabaseball.com','www.nba.com','www.kleague.com'}


def normalize_fixture(r):
    league = LEAGUES.get(r.get('league'), r.get('league'))
    teams = r.get('teams') or r.get('raw_team_names') or {}
    home, away = r.get('home') or teams.get('home'), r.get('away') or teams.get('away')
    day = r.get('match_date') or r.get('date')
    url = r.get('source_url') or r.get('outcome_source_url')
    if not (league and home and away and day and official_url(url)):
        return None
    h,a = r.get('home_score'), r.get('away_score')
    if type(h) is not int or type(a) is not int or min(h,a)<0:
        return None
    ident = r.get('official_id') or r.get('event_id') or f'{league}:{day}:{canonical(home,league)}:{canonical(away,league)}'
    return dict(league=league,match_date=day,home=home,away=away,home_score=h,away_score=a,
                fixture_id=ident,result_source_url=url,result_evidence='official_exact_fixture',
                explicit_official_id=bool(r.get('official_id') or r.get('event_id')),
                kickoff=r.get('kickoff') or r.get('kickoff_at'),
                official_date=r.get('official_date'), raw_team_names=r.get('raw_team_names') or teams)


def join(candidates, fixtures, offers=()):
    bank = {}
    def identity(f):
        ident=f['fixture_id'].lower().removeprefix(f['league'].lower()+':')
        return ident.removeprefix('https://npb.jp') if f['league']=='NPB' else ident
    for raw in fixtures:
        f = normalize_fixture(raw)
        if f:
            key=(f['league'],f['match_date'],canonical(f['home'],f['league']),canonical(f['away'],f['league']))
            previous=bank.get(key)
            different_games=(previous and previous.get('explicit_official_id') and f.get('explicit_official_id')
                             and identity(previous)!=identity(f))
            if previous and (different_games or (previous['home_score'],previous['away_score'])!=(f['home_score'],f['away_score'])):
                bank[key]=dict(previous,conflict=True)
            elif previous and previous.get('conflict'):
                continue
            else: bank[key]=f
    idx=defaultdict(list)
    for f in bank.values():
        dates={f['match_date']}
        if f.get('official_date'):dates.add(f['official_date'])
        for day in dates:idx[(f['league'],day)].append(f)
    offerbank=defaultdict(list)
    for o in offers:
        league=LEAGUES.get(o.get('league'),o.get('league'))
        if o.get('odds') and o.get('home') and o.get('away'):
            key=(league,o.get('date') or o.get('match_date'),canonical(o['home'],league),canonical(o['away'],league))
            offerbank[key].append(o)
    output=[]
    for raw in candidates:
        r=dict(raw);league=LEAGUES.get(r.get('league'),r.get('league'));r['league']=league
        teams=r.get('teams') or []
        names=list(teams.values()) if isinstance(teams,dict) else list(teams)
        wanted={canonical(x,league) for x in names}
        hits=[]
        for f in idx.get((league,r.get('match_date')),[]):
            actual={canonical(f['home'],league),canonical(f['away'],league)}
            native={canonical(x,league) for x in f.get('raw_team_names',{}).values()}
            if wanted==actual or wanted==native:
                hits.append(f)
        # A cached KST date and fresh official local date can describe the SAME game.
        by_id={}
        for f in hits:
            ident=identity(f)
            old=by_id.get(ident)
            if old and (old.get('conflict') or (old['home_score'],old['away_score'])!=(f['home_score'],f['away_score'])):
                f=dict(f,conflict=True)
            by_id[ident]=f
        hits=list(by_id.values())
        if len(hits)!=1 or hits[0].get('conflict'):
            r['join_failure']='ambiguous_fixture' if len(hits)>1 else 'official_fixture_not_found'
            output.append(r);continue
        f=hits[0]
        side=r.get('side')
        if r.get('selected_team'):
            selected=canonical(r['selected_team'],league)
            choices=[s for s in ('home','away') if selected in {canonical(f[s],league),canonical(f.get('raw_team_names',{}).get(s,''),league)}]
            if len(choices)==1: side=choices[0]
            elif selected in {'무','무승부','draw'}: side='draw'
            else:side=None
        if isinstance(teams,dict) and side in {'home','away'} and not r.get('selected_team'):
            label=canonical(teams[side],league)
            choices=[s for s in ('home','away') if label in {canonical(f[s],league),canonical(f.get('raw_team_names',{}).get(s,''),league)}]
            side=choices[0] if len(choices)==1 else None
        if r.get('market')=='H/A, OT':r['market']='moneyline'
        r.update(f, side=side, period=r.get('period') or r.get('market_scope'))
        if r['market']=='moneyline' and league in {'KBO','NPB','MLB','KBL','NBA'} and not r.get('period'):
            # No inferred basketball period. Baseball full-game ML has draw-refund audit convention.
            if league in {'KBO','NPB','MLB'}:r['period']='full_game_including_overtime'
        key=(league,f['match_date'],canonical(f['home'],league),canonical(f['away'],league))
        matches=[o for o in offerbank.get(key,[]) if (o.get('home_score'),o.get('away_score'))==(f['home_score'],f['away_score'])]
        signatures={tuple(x['odds']) for x in matches}
        if len(signatures)==1:
            r.update(proto_odds=list(next(iter(signatures))),proto_offer_evidence='archived_exact_fixture',
                     proto_price_quality='retrospective_archive_not_purchase_time_certified')
        elif len(signatures)>1:r['proto_join_failure']='conflicting_reissue_prices'
        output.append(r)
    return output
