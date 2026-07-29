// 공용 조각들. 세 페이지가 같은 카드·배지·네비를 쓴다.
// (예전엔 nav/card/badge 마크업이 3개 HTML 에 복붙돼 있었다)

export const NAV = [
  { href: "index.html", label: "가격 분석" },
  { href: "markets.html", label: "경기 분석" },
  { href: "research.html", label: "연구 현황" },
];

export function Nav({ current }) {
  return (
    <nav className="flex flex-wrap gap-5 border-b border-line py-4 text-sm">
      {NAV.map((n) => (
        <a
          key={n.href}
          href={n.href}
          className={
            n.href === current
              ? "border-b-2 border-tx pb-[3px] font-semibold text-tx no-underline"
              : "text-tx2 no-underline hover:text-tx"
          }
        >
          {n.label}
        </a>
      ))}
    </nav>
  );
}

export function Card({ className = "", as: As = "div", ...rest }) {
  return (
    <As
      className={`rounded-card border border-line bg-panel ${className}`}
      {...rest}
    />
  );
}

export function SectionTitle({ children, note }) {
  return (
    <div className="mt-7 mb-2.5 flex items-baseline gap-2.5">
      <h2 className="m-0 text-[15px] tracking-[-.01em]">{children}</h2>
      {note && <span className="text-[11.5px] text-tx3">{note}</span>}
    </div>
  );
}

const GRADE_BG = {
  A: "bg-pos text-white",
  B: "bg-accent text-white",
  C: "bg-warn text-white",
  D: "bg-neg text-white",
  T: "bg-tx3 text-white",
  U: "border border-dashed border-tx3 text-tx3",
};

/** 등급 배지 — 표 안에서 쓴다 */
export function GradeBadge({ grade, title }) {
  return (
    <span
      title={title}
      className={`mr-[7px] inline-block h-4 w-4 rounded text-center text-[10px] font-bold leading-4 ${
        GRADE_BG[grade] ?? GRADE_BG.U
      }`}
    >
      {grade}
    </span>
  );
}

const OB_BORDER = {
  A: "border-pos",
  B: "border-accent",
  C: "border-warn",
  D: "border-neg",
  U: "border-tx3 border-dashed",
};

/** 배당 버튼 — 접힌 경기 줄에서 쓴다. 등급을 테두리 색으로 보여준다. */
export function OddsChip({ label, value, grade = "U", title }) {
  return (
    <span
      title={title}
      className={`flex min-w-14 flex-col items-center rounded-[7px] border px-2 py-1 ${
        OB_BORDER[grade] ?? OB_BORDER.U
      }`}
    >
      <span className="text-[9.5px] tracking-[.03em] text-tx3">{label}</span>
      <span className="tnum text-[12.5px] font-semibold leading-[1.35]">
        {value}
      </span>
    </span>
  );
}

export function Stat({ k, v, tone }) {
  return (
    <div>
      <div className="text-[11px] text-tx3">{k}</div>
      <div
        className={`tnum text-[19px] font-semibold leading-[1.35] ${
          tone === "neg" ? "text-neg" : ""
        }`}
      >
        {v}
      </div>
    </div>
  );
}
