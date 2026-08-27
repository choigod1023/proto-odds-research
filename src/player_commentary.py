"""공식 선수정보를 기존 경기 프리뷰 문장에 안전하게 합친다."""
from __future__ import annotations


def _present(value) -> bool:
    return value is not None and value != ""


def _starter(info: dict, side: str) -> dict | None:
    detail = info.get(f"{side}_detail")
    if detail and detail.get("name"):
        return detail
    name = info.get(side)
    return {"name": name} if name else None


def _starter_label(starter: dict) -> str:
    stats = starter.get("stats") or {}
    bits = []
    if stats.get("period"):
        bits.append(str(stats["period"]))
    if stats.get("record"):
        bits.append(str(stats["record"]))
    elif _present(stats.get("wins")) or _present(stats.get("losses")):
        bits.append(f"{stats.get('wins') or 0}승 {stats.get('losses') or 0}패")
    if _present(stats.get("era")):
        bits.append(f"ERA {float(stats['era']):.2f}")
    if _present(stats.get("whip")):
        bits.append(f"WHIP {float(stats['whip']):.2f}")
    return f"{starter['name']}({', '.join(bits)})" if bits else str(starter["name"])


def _starter_sentence(home: str, away: str, info: dict) -> str | None:
    home_starter, away_starter = _starter(info, "home"), _starter(info, "away")
    if home_starter and away_starter:
        return (f"선발 맞대결은 {home} {_starter_label(home_starter)}, "
                f"{away} {_starter_label(away_starter)}로 잡혔습니다.")
    if home_starter:
        return f"{home} 선발은 {_starter_label(home_starter)}입니다."
    if away_starter:
        return f"{away} 선발은 {_starter_label(away_starter)}입니다."
    return None


def _best_hitter(players: list[dict] | None) -> dict | None:
    hitters = [player for player in (players or [])
               if player.get("name") and player.get("position") != "투수"]
    if not hitters:
        return None
    return max(hitters, key=lambda player: (
        float((player.get("stats") or {}).get("ops") or -1),
        int((player.get("stats") or {}).get("home_runs") or -1),
        int((player.get("stats") or {}).get("rbi") or -1),
        -int(player.get("order") or 99),
    ))


def _hitter_label(player: dict) -> str:
    prefix = f"{player.get('order')}번 " if player.get("order") else ""
    stats = player.get("stats") or {}
    bits = []
    if _present(stats.get("avg")):
        bits.append(f"타율 {float(stats['avg']):.3f}".replace("0.", ".", 1))
    if _present(stats.get("home_runs")):
        bits.append(f"{stats['home_runs']}홈런")
    if _present(stats.get("ops")):
        bits.append(f"OPS {float(stats['ops']):.3f}".replace("0.", ".", 1))
    suffix = f"({', '.join(bits)})" if bits else ""
    return f"{prefix}{player['name']}{suffix}"


def _lineup_sentence(home: str, away: str, info: dict) -> str | None:
    lineups = info.get("lineups") or {}
    hitters = [(home, _best_hitter(lineups.get("home"))),
               (away, _best_hitter(lineups.get("away")))]
    labels = [f"{team} {_hitter_label(player)}" for team, player in hitters if player]
    if not labels:
        return None
    state = (info.get("lineup_status") or {}).get("state")
    joined = ", ".join(labels)
    if state == "official_today":
        return f"오늘 공식 타순의 팀별 OPS 상위 타자는 {joined}입니다."
    if state == "mixed_official_projected":
        return (f"현재 타순 정보는 오늘 공식 명단과 최근 경기 기반 예상 명단이 섞여 있으며, "
                f"팀별 OPS 상위 타자는 {joined}입니다.")
    return (f"최근 공식 경기 기반 예상 타순의 팀별 OPS 상위 타자는 {joined}입니다. "
            "해당 경기의 확정 명단은 아닙니다.")


def _generic_player_label(player: dict, sport: str) -> str:
    bits = []
    games = player.get("games", player.get("apps"))
    if _present(games):
        bits.append(f"{games}경기")
    if sport == "sc":
        if _present(player.get("goals")):
            bits.append(f"{player['goals']}골")
        if _present(player.get("assists")):
            bits.append(f"{player['assists']}도움")
    elif sport == "bk":
        if _present(player.get("points")):
            bits.append(f"평균 {player['points']}점")
        if _present(player.get("rebounds")):
            bits.append(f"{player['rebounds']}리바운드")
    elif sport == "vl":
        if _present(player.get("points")):
            bits.append(f"{player['points']}득점")
        if _present(player.get("blocks")):
            bits.append(f"{player['blocks']}블로킹")
    return f"{player['name']}({', '.join(bits)})" if bits else str(player["name"])


def _key_player_sentence(home: str, away: str, sport: str, info: dict) -> str | None:
    key_players = info.get("key_players") or {}
    labels = []
    for side, team in (("home", home), ("away", away)):
        player = next((row for row in (key_players.get(side) or []) if row.get("name")), None)
        if player:
            labels.append(f"{team} {_generic_player_label(player, sport)}")
    return f"공식 시즌 기록의 팀별 주요 선수는 {', '.join(labels)}입니다." if labels else None


def _unavailable_sentence(home: str, away: str, info: dict) -> str | None:
    unavailable = info.get("unavailable") or {}
    labels = []
    for side, team in (("home", home), ("away", away)):
        rows = [row for row in (unavailable.get(side) or []) if row.get("name")][:2]
        for row in rows:
            status = row.get("status") or "출전 불가"
            labels.append(f"{team} {row['name']}({status})")
    return f"공식 출전 상태에 표시된 선수는 {', '.join(labels)}입니다." if labels else None


def player_context_text(home: str, away: str, sport: str, info: dict | None) -> str:
    """사실 확인된 선수정보만 0~3개 문장으로 압축한다."""
    info = info or {}
    sentences = []
    if sport == "bs":
        sentences.extend((_starter_sentence(home, away, info),
                          _lineup_sentence(home, away, info)))
    else:
        sentences.append(_key_player_sentence(home, away, sport, info))
    sentences.append(_unavailable_sentence(home, away, info))
    return " ".join(sentence for sentence in sentences if sentence)


def with_player_context(base: str | None, home: str, away: str,
                        sport: str, info: dict | None) -> str | None:
    """시장 결론 첫 문장 뒤에 최신 선수 문맥을 넣는다."""
    if not base:
        return base
    context = player_context_text(home, away, sport, info)
    if not context:
        return base
    cut = base.find(". ")
    if cut < 0:
        return f"{base.rstrip()} {context}".strip()
    return f"{base[:cut + 1]} {context} {base[cut + 2:]}".strip()
