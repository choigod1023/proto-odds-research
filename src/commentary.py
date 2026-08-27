"""분석 코멘트 생성 — 프리뷰 기사체.

실제 KBO 프리뷰 기사의 구성을 따른다:
    1) 각 팀 최근 흐름을 **구체적 스코어**로 서술
    2) 연승/연패·홈원정 성적 등 상태 진단
    3) 시즌 상대전적
    4) 모델 판단과 시장 가격 비교
    5) "반대 근거: ~" 로 반박 여지 제시

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


def _projection(fh: Form | None, fa: Form | None, sport: str,
                line: float | None = None, describe_gap: bool = True) -> str | None:
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

    unit = "골" if sport == "sc" else "점"

    # 종목별 기본선을 실제 발매선인 것처럼 쓰면 안 된다. 실제 U/O 라인이 있을 때만
    # 비교하고, 없으면 단순 합계 추정치만 말한다.
    if line is not None:
        ou = "오버" if total > line else "언더"
        s = (f"단순 최근 지표로 계산한 합계는 {total:.1f}{unit} 안팎이다. "
             f"실제 기준선은 {line:g}{unit}이고 최근 득실 계산 결과는 {ou}다")
    else:
        s = f"단순 최근 지표로 계산한 합계는 {total:.1f}{unit} 안팎이다"

    # 기대득점 차는 승리 마진의 확률이 아니다. 평균 차 1.3을 보고 "2점차 이상이
    # 유력"하다고 쓰던 오류를 막는다. 마진밴드 확률이 없을 때만 평균 차를 묘사한다.
    if describe_gap:
        side = "홈" if eh > ea else "원정"
        if gap < 0.35:
            s += f". 평균 득점 차는 {gap:.1f}{unit}로 거의 갈리지 않는다"
        else:
            s += f". 평균 득점 추정치는 {side}이 {gap:.1f}{unit} 앞선다"
    return s


def _market_tension(home: str, away: str, p_model: float, p_market: float,
                    context: dict | None) -> str | None:
    """시장 정배 확신과 같은 득점분포의 교차 마켓 충돌을 '의외성'으로 설명한다.

    실제 투표량이 없으므로 사람 쏠림을 단정하지 않는다. 이 값은 역배 추천 확률이
    아니라, 시장의 확신을 다시 볼 이유가 몇 개나 겹치는지를 보여 주는 진단이다.
    """
    context = context or {}
    favorite_home = p_market >= 0.5
    favorite = home if favorite_home else away
    underdog = away if favorite_home else home
    p_fav_market = p_market if favorite_home else 1.0 - p_market
    p_fav_model = p_model if favorite_home else 1.0 - p_model
    premium = p_fav_market - p_fav_model
    if premium < 0.08:
        return None

    bits = [
        f"어라 포인트는 시장의 확신이 득점분포의 교차 마켓 진단보다 강하다는 점이다. "
        f"시장은 {favorite}를 {p_fav_market*100:.0f}%로 보지만 검증 전 득점 모델은 {p_fav_model*100:.0f}%로 "
        f"{premium*100:.0f}%p 낮게 본다"
    ]
    routes = 0

    handicap = context.get("handicap") or {}
    dog_key = "home" if not favorite_home else "away"
    dog_market = handicap.get(f"{dog_key}_market")
    dog_model = handicap.get(f"{dog_key}_model")
    if dog_market is not None and dog_model is not None and dog_model - dog_market >= 0.08:
        bits.append(
            f"{underdog} 쪽 핸디캡({handicap.get('label', '실제 라인')}) 커버도 "
            f"시장 {dog_market*100:.0f}%보다 모델 {dog_model*100:.0f}%가 높다"
        )
        routes += 1

    margin = context.get("margin_band") or {}
    close_market = margin.get("close_market")
    close_model = margin.get("close_model")
    if close_market is not None and close_model is not None and close_model - close_market >= 0.08:
        close_label = margin.get("label") or "접전"
        bits.append(
            f"{close_label} 확률도 시장 {close_market*100:.0f}%와 모델 {close_model*100:.0f}%가 엇갈린다"
        )
        routes += 1

    # 승패 모델 하나만 시장과 다르면 모델 오류일 가능성이 더 크다. 같은 득점분포를
    # 핸디캡/마진에 투영해도 접전 방향이 확인될 때만 독자에게 '어라'라고 말한다.
    if not routes:
        return None
    bits.append("모델이 계산한 역배 경로는 정배가 압도당하는 그림이 아니라 접전이 길어져 한 번의 득점으로 뒤집히는 그림이다")
    bits.append("실제 투표량이 없어 쏠림 여부는 판정하지 않는다. 화면 표시는 '쏠림 의심'이다")
    return ". ".join(bits)


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
    """우세로 본 쪽과 반대되는 근거를 주체와 함께 명시한다."""
    under = fa if side == home else fh
    uname = away if side == home else home
    if not under:
        return None
    if under.streak_kind == "연승" and under.streak_n >= 2:
        return (f"반대 근거: {josa(uname,'은','는')} {under.streak_n}연승 중이다. "
                f"이 기록 때문에 일방적 흐름으로 판정하지 않는다")
    if under.last10.count("W") >= 6:
        return (f"반대 근거: {josa(uname,'은','는')} 최근 10경기 {under.last10_str}다. "
                f"최근 흐름은 열세로 판정하지 않는다")
    if under.avg_scored and under.avg_scored >= 5:
        return (f"반대 근거: {josa(uname,'은','는')} 최근 평균 {under.avg_scored:.1f}득점이다. "
                f"역전 시나리오에 필요한 득점력은 확인됐다")
    return None


def _pick_text(recommendation: dict, home: str, away: str, sport: str) -> str:
    """확정 추천 객체를 사용자에게 보이는 한 가지 선택으로 바꾼다."""
    market = str(recommendation.get("market") or "").strip()
    selection = str(recommendation.get("선택") or "").strip()
    label = str(recommendation.get("label") or recommendation.get("market_label") or "").strip()
    compact = selection.replace(" ", "")

    if market in ("승패", "승무패"):
        if compact in ("홈", "홈승", "승"):
            return f"{home} 승"
        if compact in ("원정", "원정승", "홈패", "패"):
            return f"{away} 승"
        if "무" in compact:
            return "무승부"

    if market in ("전반승무패", "전반승패"):
        if "홈" in compact:
            return f"{home} 전반 승"
        if "원정" in compact:
            return f"{away} 전반 승"
        if "무" in compact:
            return "전반 무승부"

    if market == "승①패":
        if compact == "홈2+":
            return f"{home} 2점 차 이상 승"
        if compact == "원정2+":
            return f"{away} 2점 차 이상 승"
        if compact == "1점차":
            return "1점 차 승부"

    if market == "승⑤패":
        if compact == "홈6+":
            return f"{home} 6점 차 이상 승"
        if compact == "원정6+":
            return f"{away} 6점 차 이상 승"
        if compact in ("5점차", "5점차이내"):
            return "5점 차 이내 승부"

    line = recommendation.get("line")
    unit = "골" if sport == "sc" else "점"
    if market in ("언더오버", "전반언더오버"):
        line_text = f"{float(line):g}{unit}" if line is not None else label
        period = "전반 " if market == "전반언더오버" else ""
        return f"{period}{line_text} {selection.replace('전반', '')}".strip()

    if market in ("핸디캡", "전반핸디캡"):
        if "홈" in compact or compact in ("핸디승",):
            side = home
        elif "원정" in compact or compact in ("핸디패",):
            side = away
        elif "무" in compact:
            side = "무승부"
        else:
            side = selection
        period = "전반 " if market == "전반핸디캡" else ""
        applied = label.removeprefix("H").strip()
        if not applied and line is not None:
            applied = f"{float(line):+g}"
        result = "무승부" if side == "무승부" else f"{side} 쪽"
        return f"{period}홈팀 {home} {applied} 적용 후 {result}".strip()

    return " ".join(part for part in (market, label, selection) if part)


def decision_summary(recommendation: dict | None, home: str, away: str,
                     sport: str) -> str | None:
    """서비스가 계산을 끝낸 선택을 단정형으로 고정한다.

    경기 결과를 보장하는 문장이 아니다. `무엇을 선택했는가`만 확정하고 결과의
    불확실성은 모델확률과 시장확률 숫자로 그대로 드러낸다. 이 문장은 LLM 교정 뒤에
    붙여 말투가 다시 완곡해지거나 실제 추천과 다른 마켓을 말하지 못하게 한다.
    """
    if not recommendation:
        return None
    choice = _pick_text(recommendation, home, away, sport)
    if not choice:
        return None

    sentences = [f"산출 시점의 최종 선택은 {josa(choice, '이다', '다')}."]
    model = recommendation.get("모델확률")
    market = recommendation.get("시장확률")
    odds = recommendation.get("배당")
    expected = recommendation.get("예상손익")
    figures = []
    if model is not None:
        figures.append(f"모델확률은 {float(model)*100:.1f}%")
        figures.append(f"실패확률은 {(1-float(model))*100:.1f}%")
    if market is not None:
        figures.append(f"시장확률은 {float(market)*100:.1f}%")
    if odds is not None:
        figures.append(f"배당은 {float(odds):.2f}배")
    if expected is not None:
        figures.append(f"기대수익은 {float(expected)*100:+.1f}%")
    if figures:
        sentences.append(", ".join(figures) + "다.")
    return " ".join(sentences)


def make_preview(home: str, away: str, league: str,
                 fh: Form | None, fa: Form | None, h2h: dict,
                 p_model: float | None, p_market: float | None, odds_home: float,
                 odds_away: float, payout: float, ev_home: float,
                 ev_away: float, sport: str = "bs", limit: int = 760,
                 market_context: dict | None = None) -> str:
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

    context = market_context or {}
    total_line = (context.get("total") or {}).get("line")
    pj = _projection(fh, fa, sport, total_line, describe_gap=not bool(context.get("margin_band")))
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
    no_form = (fh is None or not fh.recent_games) and (fa is None or not fa.recent_games)

    if p_market is None:
        head = "승패 배당은 아직 발표되지 않았다."
    else:
        pk = p_market * 100
        up, o_up = (home, odds_home) if pk >= 50 else (away, odds_away)
        v = pk if up == home else 100 - pk
        if abs(pk - 50) < 3:
            head = (f"승패 시장은 두 팀을 사실상 동률로 가격했다. 1순위는 {up} 승이고 "
                    f"시장확률은 {v:.0f}%")
        else:
            head = f"승패 시장 1순위는 {up} 승이다. 시장확률은 {v:.0f}%"
        head += f", 배당은 {o_up:.2f}다." if o_up else "."
    if no_form:
        head += " 양 팀 모두 최근 기록이 없다. 경기력 판정은 하지 않는다."

    side = home if p_market is not None and p_market >= 0.5 else away
    cp = _counterpoint(fh, fa, home, away, side) if p_market is not None else None
    tension = (_market_tension(home, away, p_model, p_market, context)
               if p_model is not None and p_market is not None else None)
    front = [head]
    if tension:
        front.append(tension + ".")
    if cp:
        front.append(cp + ".")
    parts = front + parts
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
    s = (f"{head}. 최종 모델 선택은 {side}다. "
         f"모델확률은 {p_model*100:.0f}%, 기대값은 {ev*100:+.1f}%다.")
    return s[:limit - 1] + "…" if len(s) > limit else s
