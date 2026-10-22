"""Read cached team PA totals literally; never repair attribution to force a match."""
from collections import Counter


def reconcile(feed, rows):
    plays = feed['liveData']['plays']['allPlays']
    by_index = {p['about'].get('atBatIndex'): p for p in plays}
    result = []
    for side, half in (('away', 'top'), ('home', 'bottom')):
        team = feed['liveData'].get('boxscore', {}).get('teams', {}).get(side, {})
        expected = team.get('teamStats', {}).get('batting', {}).get('plateAppearances')
        accepted = [r for r in rows if by_index[r['pa']]['about'].get('halfInning') == half]
        actual_players = Counter(r['batter'] for r in accepted)
        box_players = {p['person']['id']: p.get('stats', {}).get('batting', {}).get('plateAppearances', 0)
                       for p in team.get('players', {}).values()}
        differences = [dict(player=p, accepted=actual_players[p], boxscore=box_players.get(p),
                            delta=actual_players[p] - box_players[p] if p in box_players else None)
                       for p in sorted(set(actual_players) | set(box_players))
                       if actual_players[p] != box_players.get(p)]
        affected = {p['player'] for p in differences}
        delta = len(accepted) - expected if type(expected) is int else None
        # Keep every play on mismatched teams, and affected batters otherwise.
        evidence = [p for p in plays if p.get('about', {}).get('halfInning') == half
                    and (delta != 0 or p.get('matchup', {}).get('batter', {}).get('id') in affected)]
        result.append(dict(game=feed['gamePk'], side=side, accepted=len(accepted),
                           boxscore_pa=expected, delta=delta,
                           status='matched' if delta == 0 else 'missing' if delta is None else 'mismatch',
                           player_differences=differences, evidence=evidence))
    unknown = [r['pa'] for r in rows if by_index[r['pa']]['about'].get('halfInning') not in ('top', 'bottom')]
    if unknown:
        result.append(dict(game=feed['gamePk'], side='unknown', accepted=len(unknown),
                           boxscore_pa=None, delta=None, status='unattributed',
                           player_differences=[], evidence=unknown))
    return result
