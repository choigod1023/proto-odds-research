// 공용 조각들. 네 페이지가 같은 네비·카드·배지를 쓴다.
// (예전엔 nav/card/badge 마크업이 3개 HTML 에 복붙돼 있었다)
//
// ⚠️ 색은 토큰만 쓴다. 특히 **값에는 심각도 램프(sev0~sev3)만** 쓰고
//    signal 은 링크·포커스 같은 구조에만 쓴다. 초록은 팔레트에 아예 없다 —
//    이 페이지의 모든 숫자가 손실이라, 초록을 칠하면 −18.7% 가 이득처럼 읽힌다.

export const NAV = [
  { href: "research.html", label: "모델 검증" },
  { href: "lotto.html", label: "로또 검증" },
];

export function Nav({ current }) {
  return (
    <nav className="site-nav" aria-label="주요 메뉴">
      <a className="site-brand" href="markets.html">경기·배당</a>
      <div className="site-nav-links">
        {NAV.map((n) => (
          <a key={n.href} href={n.href}
            aria-current={n.href === current ? "page" : undefined}
            className="site-nav-link">
            {n.label}
          </a>
        ))}
      </div>
    </nav>
  );
}

export function Card({ className = "", as: As = "div", ...rest }) {
  return (
    <As
      className={`card rounded-card border border-rule bg-panel ${className}`}
      {...rest}
    />
  );
}

export function SectionTitle({ children, note }) {
  return (
    <div className="mt-7 mb-2.5 flex items-baseline gap-2.5">
      <h2 className="m-0 text-[15px] tracking-[-.01em]">{children}</h2>
      {note && <span className="tick">{note}</span>}
    </div>
  );
}

/* 등급 → 심각도. 한 방향 스케일이다: A 가 '좋다' 가 아니라 '덜 나쁘다'. */
const SEV_BG = {
  A: "bg-sev0 text-white",
  B: "bg-sev1 text-white",
  C: "bg-sev2 text-white",
  D: "bg-sev3 text-white",
  T: "bg-ink3 text-white", // 양쪽 동률 — 고를 근거가 없다
  U: "border border-dashed border-ink3 text-ink3", // 등급 보류
};
const SEV_BORDER = {
  A: "border-sev0",
  B: "border-sev1",
  C: "border-sev2",
  D: "border-sev3",
  U: "border-ink3 border-dashed",
};

/** 등급 배지 — 표 안에서 쓴다 */
export function GradeBadge({ grade, title }) {
  return (
    <span
      title={title}
      className={`mr-[7px] inline-block h-4 w-4 rounded-[3px] text-center text-[10px] font-bold leading-4 ${
        SEV_BG[grade] ?? SEV_BG.U
      }`}
    >
      {grade}
    </span>
  );
}

/**
 * 배당 버튼 — 접힌 경기 줄에서 쓴다.
 * 등급을 **왼쪽 세로 눈금**으로 표시한다. 테두리 전체를 물들이면 카드마다
 * 색이 튀어 목록이 소란스러워진다. 눈금이면 훑을 때 세로로 정렬돼 읽힌다.
 */
export function OddsChip({ label, value, grade = "U", title }) {
  const g = SEV_BORDER[grade] ? grade : "U";
  return (
    <span
      title={title}
      className={`flex min-w-14 flex-col items-center rounded-[6px] border border-rule border-l-[3px] px-2 py-1 ${SEV_BORDER[g]}`}
    >
      <span className="text-[9.5px] tracking-[.03em] text-ink3">{label}</span>
      <span className="tnum text-[12.5px] font-semibold leading-[1.35]">
        {value}
      </span>
    </span>
  );
}

/** 테마 토글 — 시스템 설정을 기본으로 두고 사용자가 뒤집을 수 있다.
 *  data-theme 이 미디어쿼리를 양방향으로 이겨야 하므로 root 에 스탬프한다. */
export function ThemeToggle() {
  const flip = () => {
    const r = document.documentElement;
    const cur =
      r.getAttribute("data-theme") ||
      (matchMedia("(prefers-color-scheme:dark)").matches ? "dark" : "light");
    r.setAttribute("data-theme", cur === "dark" ? "light" : "dark");
  };
  return (
    <button
      onClick={flip}
      aria-label="밝기 전환"
      title="밝기 전환"
      className="fixed bottom-5 right-5 z-20 h-9 w-9 rounded-full border border-rule bg-panel text-[15px] text-ink2 hover:text-ink"
    >
      ◐
    </button>
  );
}

export function Stat({ k, v, tone }) {
  return (
    <div>
      <div className="tick">{k}</div>
      <div
        className={`tnum text-[19px] font-semibold leading-[1.35] ${
          tone === "sev" ? "text-sev3" : ""
        }`}
      >
        {v}
      </div>
    </div>
  );
}
