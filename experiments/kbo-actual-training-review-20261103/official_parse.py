"""Conservative readers for independently inspecting KBO response bytes.

These extract names, not resolved player IDs or proof of historical receipt.
The caller must retain and verify the request-to-response provenance.
"""
import hashlib
import json


def _texts(row):
    return [str(cell.get('Text', '')).strip() for cell in row['row']]


def confirmed_lineups(body, game_id, home, away, season, series=0):
    data = json.loads(body)
    if len(data) != 5 or data[0] != [{'LINEUP_CK': True}]:
        raise ValueError('official lineup is not confirmed')
    result = {}
    for meta, encoded, team in ((data[1], data[3], home), (data[2], data[4], away)):
        if len(meta) != 1 or len(encoded) != 1:
            raise ValueError('unexpected lineup response')
        m = meta[0]
        if (m.get('G_ID'), m.get('T_ID'), m.get('SEASON_ID'), m.get('SR_ID')) != (game_id, team, season, series):
            raise ValueError('lineup fixture or team mismatch')
        rows = [_texts(row) for row in json.loads(encoded[0])['rows']]
        if len(rows) != 9 or any(len(row) < 3 for row in rows):
            raise ValueError('invalid lineup row count')
        try:
            ordered = sorted((int(row[0]), row[2]) for row in rows)
        except ValueError as exc:
            raise ValueError('invalid order') from exc
        if [n for n, _ in ordered] != list(range(1, 10)) or any(not name for _, name in ordered) or len({name for _, name in ordered}) != 9:
            raise ValueError('ambiguous lineup')
        result[team] = ordered
    return {'sha256': hashlib.sha256(body).hexdigest(), 'game_id': game_id, 'confirmed_lineups': result}


def explicit_starting_pitchers(body, *, requested_game_id, away, home):
    if not requested_game_id or away == home:
        raise ValueError('fixture provenance and distinct teams required')
    data = json.loads(body)
    if len(data['arrPitcher']) != 2:
        raise ValueError('unexpected pitcher tables')
    result = {}
    for team, entry in zip((away, home), data['arrPitcher']):
        table = json.loads(entry['table'])
        headers = _texts(table['headers'][0])
        if headers[:2] != ['선수명', '등판']:
            raise ValueError('pitcher table schema changed')
        rows = [_texts(row) for row in table['rows']]
        starts = [row[0] for row in rows if len(row) >= 2 and row[1] == '선발']
        if len(starts) != 1 or not starts[0]:
            raise ValueError('starting pitcher ambiguous')
        result[team] = starts[0]
    return {'sha256': hashlib.sha256(body).hexdigest(), 'requested_game_id': requested_game_id, 'starter_names': result}
