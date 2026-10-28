"""Single-game explicit final winner directions; never infer from preview prose."""
from datetime import datetime
import hashlib
import json
import re
from urllib.parse import unquote


def parse_golfmaster(url, html, observed_at, soup):
    from public_tipster_votes import SOURCE, KST, stamp, team, analysis_fingerprint
    metadata = []
    for node in soup.select('script[type="application/ld+json"]'):
        item = json.loads(node.get_text())
        if isinstance(item, dict) and item.get('@type') == 'BlogPosting':
            metadata.append(item)
    if len(metadata) != 1:
        raise ValueError('missing_article_metadata')
    article = metadata[0]
    if not article.get('url') or unquote(article['url']).rstrip('/') != unquote(url).rstrip('/'):
        raise ValueError('article_url_mismatch')
    published = stamp(article['datePublished'])
    observed = stamp(observed_at)
    if published > observed:
        raise ValueError('future_publication')
    author = (article.get('author') or {}).get('name')
    body = soup.select_one('.contents_style')
    if not author or body is None:
        raise ValueError('missing_author_or_body')
    headline = article.get('headline') or ''
    league_names = {'세리에 A': ('세리에A', 'sc'), 'EFL컵': ('잉리그컵', 'sc'),
                    'K리그1': ('K리그1', 'sc'), 'K리그2': ('K리그2', 'sc'),
                    'KBO': ('KBO', 'bs'), 'NPB': ('NPB', 'bs'), 'MLB': ('MLB', 'bs'),
                    'KBL': ('KBL', 'bk'), 'NBA': ('NBA', 'bk')}
    leagues = {value for name, value in league_names.items() if name in headline}
    if len(leagues) != 1:
        raise ValueError('unknown_league')
    league, sport = leagues.pop()
    sides = {}
    for node in body.select('h2,h3,h4'):
        m = re.fullmatch(r'(.+?)\s*\((Home|Away)\)', node.get_text(' ', strip=True), re.I)
        if m:
            side = m[2].lower()
            if side in sides:
                raise ValueError('multiple_fixtures')
            sides[side] = m[1].strip()
    if set(sides) != {'home', 'away'}:
        raise ValueError('missing_home_away')
    # Require the explicit Korean kickoff, not a publication date or local venue time.
    text = body.get_text(' ', strip=True)
    match = re.search(r'일시\s*[:：]\s*(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일'
                      r'\([^)]*\)\s*(\d{1,2})\s*:\s*(\d{2})\s*\((?:한국\s*시간\s*)?KST\)', text)
    if not match:
        raise ValueError('missing_explicit_kst_kickoff')
    scheduled = datetime(*map(int, match.groups()), tzinfo=KST)
    finals = [node for node in body.select('h2,h3')
              if re.search('최종.*(?:결론|추천)', node.get_text(' ', strip=True))]
    if len(finals) != 1:
        raise ValueError('missing_fullgame_section')
    lines = []
    for node in finals[0].next_siblings:
        if getattr(node, 'name', None) in ('h2', 'h3'):
            break
        if hasattr(node, 'select') and node.select('li'):
            lines.extend(li.get_text(' ', strip=True) for li in node.select('li'))
        elif hasattr(node, 'get_text'):
            lines.append(node.get_text(' ', strip=True))
    # Soccer must explicitly mean 90 minutes, never qualification/extra time.
    label = r'정규시간\s*90분\s*기본\s*방향' if sport == 'sc' else r'(?:승패|승무패)'
    picks = [m for line in lines if (m := re.fullmatch(label + r'\s*[:：]\s*(.{1,200})', line))]
    if len(picks) != 1:
        raise ValueError('missing_explicit_pick')
    pick = picks[0]
    if re.search(r'또는|혹은|복식|더블찬스|/', pick[1]):
        raise ValueError('ambiguous_pick')
    choice = pick[1].split('(')[0].strip()
    if choice == '무승부' and sport == 'sc':
        selected = 'draw'
        choice = '무승부'
    else:
        matched = []
        for side, name in sides.items():
            # No "slight edge", double chance, handicap, first-half or score inference.
            for alias in {name, team(name, league)}:
                if re.fullmatch(re.escape(alias) + r'\s*(?:승리|승)', choice):
                    matched.append(side)
        if len(set(matched)) != 1:
            raise ValueError('not_explicit_winner')
        selected = matched[0]
        choice = sides[selected] + ' 승'
    return dict(source=SOURCE, source_url=url.rstrip('/'), author=author,
                sport=sport, league=league, home=sides['home'], away=sides['away'],
                selected=selected, selection_text=choice, kickoff=scheduled.isoformat(),
                observed_at=observed.isoformat(), published_at=published.isoformat(),
                publication_time_quality='source_claim',
                content_hash=hashlib.sha256(html.encode()).hexdigest(),
                **analysis_fingerprint(text))
