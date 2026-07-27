"""분석 코멘트 생성 — 프리뷰 기사체.

실제 KBO 프리뷰 기사의 구성을 따른다:
    1) 각 팀 최근 흐름을 **구체적 스코어**로 서술
    2) 연승/연패·홈원정 성적 등 상태 진단
    3) 시즌 상대전적
    4) 모델 판단과 시장 가격 비교
    5) "다만 ~" 으로 반박 여지 제시

⚠️ 데이터에 있는 것만 쓴다.
   프로토 아카이브에는 팀명·스코어·배당·결과만 있다.
   선발투수·부상·라인업·타율은 **없으므로 언급하지 않는다.** 지어내면 그 순간 이 도구는 쓰레기가 된다.
"""
from __future__ import annotations

from team_form import Form, h2h_text


def _has_batchim(word: str) -> bool:
    """마지막 글자에 받침이 있는가. 한글이 아니면 영문 관례로 판단."""
    if not word:
        return False
    ch = word.strip()[-1]
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3:            # 한글 음절
        return (code - 0xAC00) % 28 != 0
    # 영문·숫자 팀명(KT, LG, SSG, NC…)은 **알파벳을 한글로 읽었을 때** 받침 유무로 판단한다.
    #   받침 있음: L(엘) M(엠) N(엔) R(알) S(에스) X(엑스) Z(제트)
    #   없음:      G(지) T(티) C(씨) K(케이) A(에이) …  → 'KT가', 'LG가', 'NC가'
    if ch.isdigit():
        return ch in "0136780"        # 영 일 삼 육 칠 팔
    return ch.upper() in "LMNRSXZ"


def josa(word: str, with_b: str, without_b: str) -> str:
    """받침에 맞는 조사를 붙인다. josa('삼성','이','가') → '삼성이'"""
    return word + (with_b if _has_batchim(word) else without_b)


def _flow(f: Form | None, name: str) -> str:
    """한 팀의 최근 흐름을 한두 문장으로."""
    if f is None or not f.recent_games:
        return f"{josa(name,'은','는')} 이번 시즌 기록이 충분히 쌓이지 않았다"

    scores = ", ".join(f"{g['gf']}-{g['ga']}" for g in f.recent_games[:3])
    s = f"{josa(name,'은','는')} 최근 3경기에서 {scores}를 기록했다"

    if f.streak_n >= 2:
        s += f". {f.streak_n}{f.streak_kind} 중이며 최근 10경기 {f.last10_str}"
    else:
        s += f". 최근 10경기 {f.last10_str}"

    ac, aq = f.avg_scored, f.avg_conceded
    if ac is not None and aq is not None:
        s += f", 이 기간 평균 {ac:.1f}득점 {aq:.1f}실점이다"
    return s


def _venue(f: Form | None, name: str, is_home: bool) -> str | None:
    if f is None:
        return None
    w, l = (f.home_w, f.home_l) if is_home else (f.away_w, f.away_l)
    if w + l < 5:
        return None
    where = "홈에서" if is_home else "원정에서"
    rate = w / (w + l)
    tone = "강세다" if rate >= 0.55 else ("고전 중이다" if rate <= 0.45 else "5할 언저리다")
    return f"{josa(name,'은','는')} {where} {w}승 {l}패로 {tone}"


def _projection(fh: Form | None, fa: Form | None, sport: str) -> str | None:
    """양 팀 최근 화력·실점에서 경기 양상을 추정한다.

    홈 예상 득점 = (홈 평균득점 + 원정 평균실점) / 2   ← 공격력과 상대 수비력의 절충
    """
    if not fh or not fa:
        return None
    if None in (fh.avg_scored, fh.avg_conceded, fa.avg_scored, fa.avg_conceded):
        return None
    eh = (fh.avg_scored + fa.avg_conceded) / 2
    ea = (fa.avg_scored + fh.avg_conceded) / 2
    total = eh + ea
    gap = abs(eh - ea)

    # 종목별 '타격전' 기준선
    hi, lo = {"bs": (10.0, 7.5), "bk": (225, 205), "sc": (3.0, 2.2),
              "vl": (4.2, 3.6)}.get(sport, (10.0, 7.5))
    unit = {"bs": "점", "bk": "점", "sc": "골", "vl": "세트"}.get(sport, "점")

    if total >= hi:
        tone = "양 팀 다 화력이 올라와 있어 점수가 오가는 흐름이 예상된다"
    elif total <= lo:
        tone = "양 팀 다 최근 실점이 적어 낮은 스코어 승부가 될 공산이 크다"
    else:
        tone = "평범한 스코어 흐름이 예상된다"

    s = (f"최근 화력과 실점을 겹쳐 보면 {eh:.1f}{unit} 대 {ea:.1f}{unit}, "
         f"합계 {total:.1f}{unit} 규모다. {tone}")
    if gap < 0.6:
        s += ". 예상 득점 차가 크지 않아 접전 가능성이 높다"
    return s


def _counterpoint(fh: Form | None, fa: Form | None, home: str, away: str,
                  side: str) -> str | None:
    """우세로 본 쪽의 반대 근거 — 프리뷰 기사의 '다만 ~' 자리."""
    under = fa if side == home else fh
    uname = away if side == home else home
    if not under:
        return None
    if under.streak_kind == "연승" and under.streak_n >= 2:
        return (f"다만 {josa(uname,'은','는')} {under.streak_n}연승으로 분위기를 타고 있어 "
                f"일방적으로 흐른다고 보긴 어렵다")
    if under.last10.count("W") >= 6:
        return (f"다만 {josa(uname,'은','는')} 최근 10경기 {under.last10_str}로 "
                f"흐름 자체는 나쁘지 않다")
    if under.avg_scored and under.avg_scored >= 5:
        return (f"다만 {josa(uname,'은','는')} 최근 평균 {under.avg_scored:.1f}득점으로 "
                f"한 번 터지면 뒤집을 화력은 갖고 있다")
    return None


def make_preview(home: str, away: str, league: str,
                 fh: Form | None, fa: Form | None, h2h: dict,
                 p_model: float, p_market: float, odds_home: float,
                 odds_away: float, payout: float, ev_home: float,
                 ev_away: float, sport: str = "bs", limit: int = 420) -> str:
    """경기 프리뷰 본문 — **경기가 어떻게 굴러갈지**를 쓴다.

    배당 구조(2-way/3-way, 환급률) 얘기는 여기 넣지 않는다.
    그건 가격 분석 페이지의 몫이고, 프리뷰에서 읽고 싶은 건 경기 내용이다.
    """
    parts = [_flow(fh, home) + ".", _flow(fa, away) + "."]

    v = _venue(fh, home, True)
    if v:
        parts.append(v + ".")
    v = _venue(fa, away, False)
    if v:
        parts.append(v + ".")

    t = h2h_text(h2h, league, home, away)
    if t:
        parts.append(t + ".")

    pj = _projection(fh, fa, sport)
    if pj:
        parts.append(pj + ".")

    side = home if ev_home >= ev_away else away
    p = p_model if side == home else 1 - p_model
    parts.append(f"종합하면 {josa(side,'의','의')} 근소 우세를 본다({p*100:.0f}%).")

    cp = _counterpoint(fh, fa, home, away, side)
    if cp:
        parts.append(cp + ".")

    out = " ".join(parts).replace("  ", " ").replace(". .", ".")
    if len(out) > limit:
        out = out[:limit - 1].rstrip().rstrip(",") + "…"
    return out


def make_short(home: str, away: str, fh: Form | None, fa: Form | None,
               p_model: float, side: str, ev: float, limit: int = 200) -> str:
    """짧은 한줄 요약 (목록용, 200자 이내)."""
    bits = []
    if fh and fh.streak_n >= 2:
        bits.append(f"{home} {fh.streak_n}{fh.streak_kind}")
    if fa and fa.streak_n >= 2:
        bits.append(f"{away} {fa.streak_n}{fa.streak_kind}")
    if fh and fh.last10:
        bits.append(f"{home} 최근10 {fh.last10_str}")
    if fa and fa.last10:
        bits.append(f"{away} 최근10 {fa.last10_str}")
    head = " · ".join(bits) if bits else "시즌 표본 부족"
    s = f"{head}. 모델은 {side} 우세({p_model*100:.0f}%), 기대값 {ev*100:+.1f}%."
    return s[:limit - 1] + "…" if len(s) > limit else s
