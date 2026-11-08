"""Pure parsing helpers adapted from the existing official KBO capture."""
import json
import re
from html.parser import HTMLParser

class ProfileParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.inside = False
        self.names = []

    def handle_starttag(self, tag, attrs):
        if tag == 'span' and dict(attrs).get('id', '').endswith('playerProfile_lblName'):
            self.inside = True

    def handle_data(self, data):
        if self.inside:
            self.names.append(data.strip())

    def handle_endtag(self, tag):
        if tag == 'span' and self.inside:
            self.inside = False

def profile_name(raw):
    p = ProfileParser()
    p.feed(raw.decode('utf-8'))
    if len(p.names) != 1 or not p.names[0]:
        raise ValueError('profile_name_missing_or_ambiguous')
    return p.names[0]

def parse_lineup(raw, game):
    body = json.loads(raw)
    if not isinstance(body, list) or len(body) != 5 or (not body[0]) or (type(body[0][0].get('LINEUP_CK')) is not bool):
        raise ValueError('invalid_lineup_envelope')
    if not body[0][0]['LINEUP_CK']:
        return None
    result = {}
    for side, m, t in [('home', 1, 3), ('away', 2, 4)]:
        meta = body[m][0]
        for k, v in [('G_ID', game['G_ID']), ('T_ID', game[side.upper() + '_ID']), ('LE_ID', game['LE_ID']), ('SR_ID', game['SR_ID']), ('SEASON_ID', game['SEASON_ID'])]:
            if str(meta.get(k)) != str(v):
                raise ValueError('lineup_identity_mismatch:' + k)
        rows = json.loads(body[t][0])['rows']
        players = [{'order': int(row['row'][0]['Text']), 'position': row['row'][1]['Text'].strip(), 'name': row['row'][2]['Text'].strip()} for row in rows]
        if sorted((p['order'] for p in players)) != list(range(1, 10)) or any((not p['position'] or not p['name'] for p in players)):
            raise ValueError('invalid_nine')
        result[side] = sorted(players, key=lambda p: p['order'])
    return result

def publication_time_fields(raw):
    """Keep explicit source claims verbatim; they do not prove historical receipt."""
    found = []

    def visit(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                p = path + [str(key)]
                upper = str(key).upper()
                if ('ANNOUNC' in upper or 'PUBLISH' in upper) and ('TIME' in upper or 'DATE' in upper or upper.endswith(('_AT', '_DT', '_TM'))):
                    if isinstance(item, (str, int, float)) and (not isinstance(item, bool)) and (item != ''):
                        found.append({'path': p, 'value': item})
                visit(item, p)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, path + [str(index)])
    visit(json.loads(raw), [])
    return found

def select_schedule_game(games, gid, home, away):
    matches = [g for g in games if (g.get('G_ID') == gid if gid is not None else (g.get('HOME_NM', '').strip(), g.get('AWAY_NM', '').strip()) == (home, away))]
    if len(matches) != 1:
        raise ValueError('game_not_uniquely_in_official_schedule')
    return matches[0]
