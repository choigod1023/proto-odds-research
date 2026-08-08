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


def _ro(word: str) -> str:
    """'로 / 으로' — 받침이 없거나 **ㄹ 받침**이면 '로', 그 외는 '으로'.
    (골→로, 세트→로, 점→으로) 한국어에서 ㄹ 은 예외라 _has_batchim 만으로는 안 된다."""
    if not word:
        return "로"
    c = ord(word[-1])
    if not (0xAC00 <= c <= 0xD7A3):
        return "로"
    jong = (c - 0xAC00) % 28
    return "로" if jong in (0, 8) else "으로"      # 8 = ㄹ


def josa(word: str, with_b: str, without_b: str) -> str:
    """받침에 맞는 조사를 붙인다. josa('삼성','이','가') → '삼성이'"""
    return word + (with_b if _has_batchim(word) else without_b)


def _flow(f: Form | None, name: str) -> str:
    """한 팀의 최근 흐름을 한두 문장으로."""
    if f is None or not f.recent_games:
        return f"{josa(name,'은','는')} 이번 시즌 기록이 충분히 쌓이지 않았다"

    # ⚠️ 예전엔 최근 3경기 스코어를 그대로 나열했다("10-9, 0-14, 8-5를 기록했다").
    #    실측: 해설 225건 중 **97%** 에 이 나열이 들어갔고, 해설 1건당 숫자가
    #    평균 45.2개까지 불었다(483자에 45개 — 열 자에 하나 꼴).
    #    스코어 세 개를 읽고 뭘 알 수 있나? 아무것도 없다. 읽는 사람이 머릿속에서
    #    다시 요약해야 한다. **요약은 여기서 해 주는 게 맞다.**
    #    숫자를 버리는 게 아니라, 숫자가 말하려던 걸 대신 말한다.
    last3 = f.recent_games[:3]
    w3 = sum(1 for g in last3 if g["gf"] > g["ga"])
    l3 = sum(1 for g in last3 if g["gf"] < g["ga"])
    gf3 = [g["gf"] for g in last3]

    if f.streak_n >= 2:
        # 연승·연패는 그 자체로 3경기 흐름을 다 말한다. 겹쳐 쓰지 않는다.
        s = f"{josa(name,'은','는')} {f.streak_n}{f.streak_kind} 중이다"
    elif w3 and l3:
        s = f"{josa(name,'은','는')} 최근 3경기를 {w3}승 {l3}패로 오락가락했다"
    elif w3 == len(last3) and last3:
        s = f"{josa(name,'은','는')} 최근 3경기를 모두 잡았다"
    elif l3 == len(last3) and last3:
        s = f"{josa(name,'은','는')} 최근 3경기를 모두 놓쳤다"
    else:
        s = f"{josa(name,'은','는')} 최근 3경기에서 승부를 가리지 못했다"

    # 기복은 평균이 못 잡는 정보다 — 같은 평균이라도 매 경기 같은 팀과
    # 어느 날만 터지는 팀은 다르게 사야 한다. 그래서 한 문장을 준다.
    if len(gf3) == 3 and max(gf3) - min(gf3) >= 5:
        # 앞 문장이 '~했다/~잡았다/~놓쳤다' 로 끝나 종결형이라, 쉼표로 이으면 비문이 된다.
        s += ". 득점이 경기마다 크게 출렁인다"

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

    # ⚠️ 예전엔 "평범한 스코어 흐름이 예상된다" 같은 문장을 넣었다.
    #    아무 말도 안 하는 문장이다. 프리뷰는 **뭐가 일어날지 특정**해야 한다.
    #    라인이 있는 마켓(언더오버·핸디캡)에 바로 대볼 수 있는 형태로 쓴다.
    line = {"bs": 8.5, "bk": 215, "sc": 2.5, "vl": 3.5}.get(sport, 8.5)
    ou = "오버" if total > line else "언더"
    # ⚠️ 예전엔 "{eh} 대 {ea}, 합계 {total} — 기준선 {line} 대비" 로 산수를 다 펼쳤다.
    #    한 문장에 숫자 넷이다. 읽는 사람이 알아야 할 건 **기준선 어느 쪽인가** 하나고,
    #    그 판단을 뒤집는 유일한 숫자는 합계다. 나머지 셋은 과정이지 결론이 아니다.
    s = (f"최근 화력과 실점을 겹쳐 보면 합계 {total:.1f}{unit} 언저리로 "
         f"기준선({line}{unit})보다 {'높다' if ou == '오버' else '낮다'} — {ou} 쪽이다")

    # 야구는 무승부가 사실상 없다(연장). 종목에 없는 결과를 말하면 안 된다.
    tie_ok = sport in ("sc",)
    side = "홈" if eh > ea else "원정"
    if gap < 0.35:
        near = f"무승부·1{unit}차" if tie_ok else f"1{unit}차"
        s += f". 득점 차 {gap:.1f}{unit}{_ro(unit)} 갈리지 않아 {near} 승부가 유력하다"
    elif gap < 0.8:
        s += f". {side}이 {gap:.1f}{unit} 앞서는 정도라 1{unit}차 접전으로 본다"
    else:
        s += f". {side}이 {gap:.1f}{unit} 앞서 2{unit}차 이상으로 갈릴 공산이 크다"
    return s


def _schedule(fh: Form | None, fa: Form | None, home: str, away: str
              ) -> str | None:
    """일정 변수 — 휴식일과 연전. 피로는 후반 집중력에 직결된다."""
    bits = []
    for f, n in ((fh, home), (fa, away)):
        if not f or f.rest_days is None:
            continue
        if f.rest_days >= 3:
            bits.append(f"{josa(n,'은','는')} {f.rest_days}일 쉬고 나온다")
        elif f.streak_days >= 5:
            bits.append(f"{josa(n,'은','는')} {f.streak_days}연전째로 체력 부담이 있다")
        elif f.rest_days <= 1 and f.streak_days >= 3:
            bits.append(f"{josa(n,'은','는')} {f.streak_days}일 연속 경기 중이다")
    return ", ".join(bits) if bits else None


def _momentum(f: Form | None, name: str) -> str | None:
    """득실 마진 추세 — 성적이 아니라 내용이 좋아지고 있는가."""
    if not f:
        return None
    t = f.trend
    if t == "상승":
        return (f"{josa(name,'은','는')} 득실 마진이 최근 5경기 "
                f"{f.margin_prev:+.1f}에서 {f.margin_recent:+.1f}로 올라오며 "
                f"내용이 좋아지고 있다")
    if t == "하락":
        return (f"{josa(name,'은','는')} 득실 마진이 {f.margin_prev:+.1f}에서 "
                f"{f.margin_recent:+.1f}로 떨어져 내용이 나빠지고 있다")
    return None


def _style(fh: Form | None, fa: Form | None, home: str, away: str
           ) -> str | None:
    """접전 성향 — 박빙으로 흐르는 팀인지."""
    bits = []
    for f, n in ((fh, home), (fa, away)):
        if not f or f.close_rate is None or len(f.last10) < 8:
            continue
        if f.close_rate >= 0.6:
            bits.append(f"{josa(n,'은','는')} 최근 10경기 중 "
                        f"{f.close_games}경기가 2점차 이내였을 만큼 접전이 잦다")
        elif f.blowout_w >= 3:
            bits.append(f"{josa(n,'은','는')} 최근 10경기 중 5점차 이상 대승이 "
                        f"{f.blowout_w}번으로 한 번 터지면 크게 벌린다")
        elif f.shutout_l >= 2:
            bits.append(f"{josa(n,'은','는')} 최근 10경기 중 무득점 패가 "
                        f"{f.shutout_l}번으로 타선이 침묵하는 경기가 있다")
    return ". ".join(bits) if bits else None


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
                 ev_away: float, sport: str = "bs", limit: int = 560) -> str:
    """경기 프리뷰 본문 — **경기가 어떻게 굴러갈지**를 쓴다.

    배당 구조(2-way/3-way, 환급률) 얘기는 여기 넣지 않는다.
    그건 가격 분석 페이지의 몫이고, 프리뷰에서 읽고 싶은 건 경기 내용이다.
    """
    # ⚠️ 순서를 고정하면 모든 경기가 똑같은 문장으로 시작해 읽는 사람이 금방 질린다.
    #    **이 경기에서 가장 특징적인 사실**을 앞에 세우고 나머지를 뒤에 붙인다.
    #    (연승·연패, 무득점 패 누적, 접전 빈도 같은 건 그 경기만의 얘기다)
    lead = _momentum(fh, home) or _momentum(fa, away) or _style(fh, fa, home, away)
    parts = []
    if lead:
        parts.append(lead + ".")
    parts += [_flow(fh, home) + ".", _flow(fa, away) + "."]

    for extra in (_venue(fh, home, True), _venue(fa, away, False),
                  h2h_text(h2h, league, home, away),
                  _schedule(fh, fa, home, away)):
        if extra:
            parts.append(extra + ".")
    for extra in (_momentum(fh, home), _momentum(fa, away),
                  _style(fh, fa, home, away)):
        if extra and (extra + ".") not in parts:
            parts.append(extra + ".")

    pj = _projection(fh, fa, sport)
    pj_over = None
    if pj:
        parts.append(pj + ".")
        pj_over = "오버" if "오버 쪽" in pj else ("언더" if "언더 쪽" in pj else None)

    # ⚠️ 결론은 **맨 앞에 하나로** 낸다.
    #    예전엔 "모델은 …로 본다" 로 끝냈는데 그건 판단이 아니라 관찰이다.
    #    읽는 사람이 원하는 건 "그래서 뭘로 보나" 하나다.
    #
    #    픽의 근거는 **모델이 아니라 시장**이다 — 모델 추천은 실측 −42.2% 였고
    #    적중률도 시장 51.9% > 모델 48.8% 다. '가장 일어날 법한 결과' 를 묻는다면
    #    답은 시장의 최저 배당 쪽이다.
    #    ⚠️ 이건 **이기는 픽이 아니라 가장 잘 맞는 픽**이다. 기대값은 여전히 음수다.
    pk = p_market * 100
    up, o_up = (home, odds_home) if pk >= 50 else (away, odds_away)
    v = pk if up == home else 100 - pk
    no_form = (fh is None or not fh.recent_games) and (fa is None or not fa.recent_games)

    if abs(pk - 50) < 3:
        # 반반이면 '픽' 이라고 부르면 안 된다. 그래도 사야 한다면 낮은 배당 쪽이다.
        head = f"양쪽이 사실상 반반이다({up} {v:.0f}%). 굳이 고르면 {up} 승"
        head += f", 배당 {o_up:.2f}." if o_up else "."
    else:
        head = f"예상 픽은 {up} 승. 적중 확률 {v:.0f}%"
        head += f" · 배당 {o_up:.2f}." if o_up else "."
    if pj_over:
        head += f" 총득점은 {pj_over} 쪽."
    if no_form:
        head += " 양 팀 모두 최근 기록이 없어 경기 내용으로는 보탤 게 없다."
    else:
        d = abs(p_model * 100 - pk)
        if d >= 15:
            head += (f" 우리 모델은 {p_model*100:.0f}%로 {d:.0f}%p 다르게 보는데, "
                     f"한쪽 팀의 최근 상대가 약해 득점이 부풀려 잡힌 경우다 — 시장 쪽을 쓴다.")

    side = home if p_market >= 0.5 else away      # 반론 문장이 쓰는 '우세 쪽'
    cp = _counterpoint(fh, fa, home, away, side)
    if cp:
        # ⚠️ 예전엔 parts 맨 뒤에 붙였다. 그래서 앞의 수치 문장들이 자리를 다 먹고
        #    상한(27% 가 잘렸다)에서 **반론이 제일 먼저 희생됐다** — 실측 28% 만 살아남았다.
        #    '다만 ~' 은 결론을 뒤집을 수도 있는 문장이라 잘려선 안 되는 쪽이다.
        #    결론 바로 뒤에 세운다. 기사에서도 그 자리다.
        parts.insert(0, cp + ".")

    parts.insert(0, head)      # 결론이 맨 앞
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
