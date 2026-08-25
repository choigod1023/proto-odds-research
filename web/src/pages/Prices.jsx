import { useMemo, useState } from "react";
import { Card, Nav, SectionTitle, Stat, ThemeToggle } from "../components/ui.jsx";
import { odds, pct } from "../lib/fmt.js";
import { usePolledData } from "../lib/poll.js";

const SPORTS = { sc: "축구", bs: "야구", bk: "농구", vl: "배구" };

export default function Prices({ embedded = false }) {
  // 열어 둔 채로도 갱신되게 한다 — 예전엔 첫 로드 한 번뿐이라 새로고침이 필요했다.
  const { data, at } = usePolledData({ d: "data/today.json" }, 300000);
  const d = data.d;
  const err = at && !d ? "불러오지 못했습니다" : null;

  /**
   * 프로토는 회차를 겹쳐 발매한다 → 같은 경기·마켓·선택이 두 회차에 다른 배당으로 걸린다.
   * 같은 결과에 더 받는 쪽이 순수하게 유리하다
   * (실측: 오버라운드 평균 1.56%p 개선 · 차익거래는 0건).
   * 회차를 안 보여주면 그냥 '중복' 으로 읽힌다 — 실제로 그랬다.
   */
  const games = useMemo(() => {
    const gs = (d?.rounds || []).flatMap((r) =>
      r.games.map((g) => ({ ...g, _rounds: [r.round] })));

    /* ① 배당 벡터가 **완전히 같은** 회차끼리는 한 줄로 합친다.
          프로토는 회차를 겹쳐 발매해서 같은 경기가 두 번 나오는데, 실측상
          겹친 마켓의 중앙값은 배당 차이가 0 이다. 그대로 두면 목록 절반이
          아무 정보 없는 중복이 된다. */
    const merged = new Map();
    for (const g of gs) {
      const k = `${g.game_no}|${g.market}|${g.market_label || ""}|` +
        (g.selections || []).map((s) => s.odds).join(",");
      const prev = merged.get(k);
      if (prev) prev._rounds.push(...g._rounds);
      else merged.set(k, g);
    }
    const out = [...merged.values()];

    /* ② 남은 중복은 **배당이 실제로 다른** 경우다. 이때는 선택지마다
          어느 회차가 더 주는지가 갈린다(홈은 89회차, 원정은 88회차 식).
          그래서 카드 단위가 아니라 **선택지 단위로** 표시해야 쓸 수 있다.
          실측: 오버라운드 평균 1.56%p 개선 · 차익거래는 0건. */
    const key = (g, s) => `${g.game_no}|${g.market}|${g.market_label || ""}|${s.name}`;
    const best = {};
    out.forEach((g) => (g.selections || []).forEach((s) => {
      const k = key(g, s);
      if (!best[k] || s.odds > best[k].odds)
        best[k] = { odds: s.odds, round: g._rounds[0] };
    }));
    out.forEach((g) => {
      g._better = (g.selections || []).map((s) => {
        const b = best[key(g, s)];
        return b && b.odds > s.odds ? b : null;
      });
    });
    return out;
  }, [d]);

  if (err) return <Shell embedded={embedded}><p className="py-8 text-sev3">데이터를 불러오지 못했습니다: {err}</p></Shell>;
  if (!d) return <Shell embedded={embedded}><p className="py-8 text-ink3">불러오는 중…</p></Shell>;

  const r0 = (d.rounds || [])[0];
  return (
    <Shell embedded={embedded} meta={<Meta d={d} />}>
      <Card className="mt-4 border-l-[3px] border-l-sev2 px-4 py-3.5">
        <b className="text-[13.5px]">먼저 알아야 할 것</b>
        <p className="mt-1.5 mb-0 text-[13px] leading-[1.75] text-ink2">
          과거 553회차를 134개 구간으로 쪼개 검정한 결과{" "}
          <b className="text-ink">수익률이 플러스인 구간은 하나도 없었다.</b>{" "}
          배당 정보만으로는 프로토를 이길 수 없다는 뜻이다. 이 페이지는 "무엇을 사라"가 아니라{" "}
          <b className="text-ink">"무엇을 피하면 덜 잃는가"</b>를 보여준다.{" "}
          <a className="text-signal" href="https://github.com/choigod1023/proto-odds-research/blob/main/findings/Q0.md">
            측정 근거 →
          </a>
        </p>
      </Card>

      <Card className="mt-3 flex flex-wrap gap-x-10 gap-y-3 px-4 py-3.5">
        <Stat k="이번 회차" v={r0 ? `${r0.round}회차` : "—"} />
        <Stat k="2-WAY 환급률" v={r0 ? `${r0.payout_2way}%` : "—"} />
        <Stat k="기대 손실" v={r0 ? pct(-(1 - r0.payout_2way / 100)) : "—"} tone="sev" />
        <Stat k="+EV 구간" v="0개" />
      </Card>
      <p className="mt-1.5 text-[11.5px] text-ink3">
        {r0 ? `${r0.n_games}개 경기 · ${r0.grade_note}` : ""} · +EV 는 134개 구간 검정 결과다
      </p>

      <GameList games={games} />
      {!embedded && <Footer />}
      {!embedded && <ThemeToggle />}
    </Shell>
  );
}

function Shell({ children, meta, embedded = false }) {
  if (embedded) return (
    <section id="price-comparison">
      <SectionTitle note="같은 선택은 더 높은 배당이 유리">배당 비교</SectionTitle>
      <p className="mb-3 text-[12px] leading-[1.7] text-ink3">
        여기서 ‘유리’는 승리 예상이 아니라 <b className="text-ink">같은 결과를 더 높은 가격에 사는 회차</b>라는 뜻입니다.
      </p>
      {meta}
      {children}
    </section>
  );
  return (
    <div className="mx-auto max-w-[960px] px-5 pb-20">
      <Nav current="index.html" />
      <header>
        <h1 className="mt-[22px] mb-1 text-[22px] tracking-[-.01em]">프로토 가격 분석</h1>
        <p className="m-0 text-[13.5px] text-ink2">
          한국 고정배당 시장을 553회차·353,047건 실측으로 분석한다. 승부 예측이 아니라{" "}
          <b>가격 분석과 회피 필터</b>다.
        </p>
        {meta}
      </header>
      {children}
    </div>
  );
}

const Meta = ({ d }) => (
  <div className="mt-1.5 text-[11.5px] text-ink3">
    발매중 {(d.rounds || []).map((r) => `${r.round}회차`).join(", ") || "없음"} ·
    분석 근거 {d.basis.history_rounds}회차 / {d.basis.history_bets.toLocaleString()}건 ·
    갱신 {String(d.generated_at || "").slice(0, 16).replace("T", " ")}
  </div>
);

const SORTS = [["time", "시간순"], ["payout", "환급률 높은 순"], ["roi", "과거 실측 좋은 순"]];
const bestRoi = (g) => Math.max(...g.selections.map((s) => s.hist_roi ?? -1));

function GameList({ games }) {
  const [f, setF] = useState({ sp: "", mk: "", safe: false, sort: "time" });
  const uniq = (a) => [...new Set(a)].filter(Boolean).sort();

  const rows = useMemo(() => {
    let gs = games.filter((g) =>
      (!f.sp || g.sport === f.sp) && (!f.mk || g.market === f.mk) &&
      (!f.safe || !g.warnings.length));
    if (f.sort === "payout") gs = [...gs].sort((a, b) => b.payout - a.payout);
    else if (f.sort === "roi") gs = [...gs].sort((a, b) => bestRoi(b) - bestRoi(a));
    else gs = [...gs].sort((a, b) => (a.date + a.game_no).localeCompare(b.date + b.game_no));
    return gs;
  }, [games, f]);

  const sel = "rounded-md border border-rule bg-panel px-[7px] py-1 text-[12px] text-ink";
  return (
    <>
      <SectionTitle note={`${rows.length}경기 / 전체 ${games.length}`}>발매 중인 경기</SectionTitle>
      <div className="sticky top-0 z-10 flex flex-wrap items-center gap-3 border-b border-rule bg-paper py-2.5 text-[12px] text-ink2">
        <label className="flex items-center gap-1.5">종목
          <select className={sel} value={f.sp} onChange={(e) => setF({ ...f, sp: e.target.value })}>
            <option value="">전체</option>
            {uniq(games.map((g) => g.sport)).map((v) => (
              <option key={v} value={v}>{SPORTS[v] || v}</option>))}
          </select></label>
        <label className="flex items-center gap-1.5">상품
          <select className={sel} value={f.mk} onChange={(e) => setF({ ...f, mk: e.target.value })}>
            <option value="">전체</option>
            {uniq(games.map((g) => g.market)).map((v) => <option key={v}>{v}</option>)}
          </select></label>
        <label className="flex items-center gap-1.5">정렬
          <select className={sel} value={f.sort} onChange={(e) => setF({ ...f, sort: e.target.value })}>
            {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select></label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={f.safe}
            onChange={(e) => setF({ ...f, safe: e.target.checked })} />
          경고 있는 경기 숨기기
        </label>
      </div>
      {rows.length ? rows.map((g, k) => <GameCard key={`${g.game_no}-${g._rounds.join("_")}-${k}`} g={g} />)
        : <p className="py-8 text-[13px] text-ink3">조건에 맞는 경기가 없습니다.</p>}
    </>
  );
}

function GameCard({ g }) {
  // 3-way 핸디캡은 환급률이 가장 나쁘다(86.8%) — 상품 자체가 불리하다는 표시
  const bad = g.booking_class === "3-way-핸디캡";
  return (
    <Card className="mt-2.5 px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-2 text-[12px] text-ink3">
        <span className="tnum">{g.game_no}</span>
        <span className="tnum rounded border border-rule px-[5px]">{g._rounds.join("·")}회차</span>
        <span>{g.date}</span>
        <span className="rounded border border-rule px-[5px]">{g.league}</span>
        <span className="text-[13.5px] font-semibold text-ink">
          {g.home} <span className="font-normal text-ink3">vs</span> {g.away}
        </span>
        <span className={`rounded px-[5px] ${bad ? "border border-sev3 text-sev3" : "border border-rule"}`}>
          {g.market}{g.market_label ? ` ${g.market_label}` : ""} · {g.booking_class}
        </span>
        <span className="tnum ml-auto">환급 {g.payout}%</span>

      </div>

      <div className="mt-2.5 grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
        {g.selections.map((s, k) => (
          <div key={k} className="rounded-[7px] border border-rule px-2.5 py-2">
            <div className="flex items-baseline justify-between text-[11.5px] text-ink3">
              <span className="text-[12.5px] font-medium text-ink">{s.name}</span>
              <span className="tnum">{s.bucket}</span>
            </div>
            <div className="tnum text-[17px] font-semibold leading-tight">{odds(s.odds)}</div>
            <div className="mt-0.5 text-[11px] text-ink3">
              시장 <span className="tnum">{pct(s.prob)}</span>
            </div>
            {g._better?.[k] && (
              <div className="mt-0.5 text-[11px] text-sev2">
                {g._better[k].round}회차는 <span className="tnum">{odds(g._better[k].odds)}</span> — 그쪽이 유리
              </div>
            )}
            {s.hist_roi != null && (
              <div className="text-[11px] text-ink3">
                과거 실측{" "}
                <span className="tnum text-sev3">{(s.hist_roi * 100).toFixed(1)}%</span>{" "}
                n={s.hist_n.toLocaleString()}
              </div>
            )}
          </div>
        ))}
      </div>

      <p className="mt-2.5 mb-0 text-[12.5px] leading-[1.75] text-ink2">{g.comment}</p>
      {!!g.warnings.length && (
        <ul className="mt-1.5 mb-0 list-none p-0 text-[11.5px] text-sev2">
          {g.warnings.map((w, k) => <li key={k}>⚠ {w}</li>)}
        </ul>
      )}
    </Card>
  );
}

const Footer = () => (
  <footer className="mt-9 border-t border-rule pt-4 text-[11.5px] leading-[1.85] text-ink3">
    <p className="m-0 mb-1.5 font-semibold text-ink2">이 페이지가 쓰는 실측 근거</p>
    <p className="m-0">· 회차마다 환급률이 86~89%로 다르다 — 회차 안에서는 기계적으로 일정(SD 0.0007)하지만 회차 간에는 13.5배 더 흔들린다</p>
    <p className="m-0">· 배당이 높을수록 손실이 커진다 — 강팀 −9.9%, 배당 5.0 이상 −33.2% (favorite-longshot 편향)</p>
    <p className="m-0">· 같은 경기라도 2-way 상품이 3-way 핸디캡보다 약 2%p 유리하다</p>
    <p className="m-0">· <b className="text-ink2">배당 1.0–1.3 만 골라 사면 −9.2%</b>, 아무거나 사면 −13.7%.
      2023~2026 네 해 모두 같은 방향 — 자세한 등급은 <a className="text-signal" href="markets.html">경기 분석</a></p>
    <p className="mt-3.5">데이터 출처 와이즈토토 회차 아카이브 · 비상업 연구 목적 ·{" "}
      <a className="text-signal" href="https://github.com/choigod1023/proto-odds-research">소스와 방법론</a></p>
    <p className="m-0">합법 발매처는 오프라인 판매점과 betman.co.kr 뿐이다. 해외 북메이커 이용은 국민체육진흥법 위반이다.</p>
  </footer>
);
