"""Reconstruct KBL stints from official text-cast and player-stat JSON arrays."""

from collections import Counter
from itertools import groupby


POINTS = {"201": 2, "203": 1, "205": 3, "207": 2}
PERIODS = tuple(f"Q{i}" for i in range(1, 5)) + tuple(f"X{i}" for i in range(1, 9))
Stint = tuple[tuple[str, ...], tuple[str, ...], int, int]


def _index_box(player_box):
    players = {}
    ids = set()
    teams = {"1": set(), "2": set()}
    time_by_side = Counter()
    starters = set()
    for row in player_box:
        person, stats = row["player"], row["records"]
        team, name, pid = person["tcode"], person["pname"], person["pcode"]
        side = str(row["homeAway"])
        if not all(isinstance(value, str) and value for value in (team, name, pid)):
            raise ValueError("missing player or team identity in box")
        if side not in teams or (team, name) in players or pid in ids:
            raise ValueError(f"ambiguous player or side in box: {team} {name}")
        minute, second, points = stats["playMin"], stats["playSec"], stats["score"]
        if (
            any(type(value) is not int or value < 0 for value in (minute, second, points))
            or second >= 60
        ):
            raise ValueError(f"invalid box totals for {pid}")
        duration = 60 * minute + second
        players[team, name] = (pid, duration, points)
        ids.add(pid)
        teams[side].add(team)
        time_by_side[side] += duration
        if str(row["startFlag"]) == "1":
            starters.add(pid)

    if any(len(codes) != 1 for codes in teams.values()) or teams["1"] == teams["2"]:
        raise ValueError("box must identify one distinct team on each side")
    home, away = next(iter(teams["1"])), next(iter(teams["2"]))
    overtime, remainder = divmod(time_by_side["1"] - 12_000, 1_500)
    if time_by_side["1"] != time_by_side["2"] or remainder or not 0 <= overtime <= 8:
        raise ValueError("box team durations do not describe a complete KBL game")
    return players, (home, away), starters, PERIODS[:4 + overtime]


def reconstruct(text_rows, player_box) -> list[Stint]:
    """Return (home_ids, away_ids, seconds, home_margin_delta) in game order.

    Both inputs are decoded official response arrays. IDs are sorted pcode strings;
    homeAway 1 is home and 2 is away. Each stint stays within one period. Scoring
    uses event order, so a lineup scoring between changes at one displayed clock
    can have a zero-second stint. Overtime uses five minutes and Xn (or EQn).

    Incomplete or inconsistent feeds raise ValueError, including disagreement
    with the box's starters, individual playing times, or individual scores.
    Box marginCn is intentionally excluded because it can contain 999.
    """
    try:
        return _replay(text_rows, *_index_box(player_box))
    except (KeyError, TypeError) as error:
        raise ValueError(f"invalid official input: {error}") from error


def _period(row):
    key = row["q"]
    return "X" + key[2:] if isinstance(key, str) and key.startswith("EQ") else key


def _replay(text_rows, players, teams, starters, periods):
    rows = sorted(text_rows, key=lambda row: row["n"])
    sequence = [row["n"] for row in rows]
    if any(type(n) is not int or n < 0 for n in sequence) or len(set(sequence)) != len(sequence):
        raise ValueError("invalid or duplicate text-cast sequence number")
    groups = [(period, list(events)) for period, events in groupby(rows, _period)]
    if tuple(period for period, _ in groups) != periods:
        raise ValueError("text-cast periods do not match box duration")

    result = []
    scored = Counter()
    for period, events in groups:
        length = 600 if period.startswith("Q") else 300
        previous = length
        floor = {team: set() for team in teams}
        started = ended = False
        stints: list[Stint] = []

        def lineup():
            home, away = (tuple(sorted(floor[team])) for team in teams)
            if len(home) != 5 or len(away) != 5:
                raise ValueError(f"{period}: expected five on court for each side")
            return home, away

        def record(seconds, margin):
            home, away = lineup()
            if stints and stints[-1][:2] == (home, away):
                _, _, old_seconds, old_margin = stints[-1]
                stints[-1] = (home, away, old_seconds + seconds, old_margin + margin)
            else:
                stints.append((home, away, seconds, margin))

        for row in events:
            minute, second = row["m"], row["s"]
            if (
                type(minute) is not int or type(second) is not int
                or minute < 0 or not 0 <= second < 60
            ):
                raise ValueError(f"{period}: invalid clock at event {row['n']}")
            clock = 60 * minute + second
            if clock > previous:
                raise ValueError(f"{period}: clock went backwards at event {row['n']}")
            if clock < previous:
                if not started or ended:
                    raise ValueError(f"{period}: clock ran outside period boundaries")
                record(previous - clock, 0)
            previous = clock

            action = row["a"]
            if action == "001":
                if started or clock != length:
                    raise ValueError(f"{period}: invalid start marker")
                home, away = lineup()
                if period == "Q1" and set(home + away) != starters:
                    raise ValueError("Q1 starters do not match box")
                started = True
            elif action == "009":
                if not started or ended or clock != 0:
                    raise ValueError(f"{period}: invalid end marker")
                lineup()
                ended = True
            elif (not started and action != "101") or (ended and action != "102"):
                raise ValueError(f"{period}: unexpected action outside period at event {row['n']}")
            elif action in ("101", "102") or action in POINTS:
                team = row["t"]
                pid = players[team, row["p"]][0]
                on_court = pid in floor[team]
                if on_court != (action != "101"):
                    raise ValueError(f"{period}: inconsistent player state at event {row['n']}")
                if action == "101":
                    floor[team].add(pid)
                elif action == "102":
                    floor[team].remove(pid)
                else:
                    points = POINTS[action]
                    scored[pid] += points
                    record(0, points if team == teams[0] else -points)

        if not ended or any(floor.values()) or sum(stint[2] for stint in stints) != length:
            raise ValueError(f"{period}: incomplete end-of-period data")
        result.extend(stints)

    played = Counter()
    for home, away, seconds, _ in result:
        for pid in home + away:
            played[pid] += seconds
    for pid, seconds, points in players.values():
        if played[pid] != seconds or scored[pid] != points:
            raise ValueError(
                f"box mismatch for {pid}: seconds {played[pid]}/{seconds}, points {scored[pid]}/{points}"
            )
    return result
