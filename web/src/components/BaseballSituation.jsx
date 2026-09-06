import React from "react";

export default function BaseballSituation({ live }) {
  const bases = live?.bases || {};
  const positions = [["second", "2루"], ["third", "3루"], ["first", "1루"]];
  const known = positions.every(([key]) => typeof bases[key]?.occupied === "boolean");
  const occupied = positions.filter(([key]) => bases[key]?.occupied).length;
  return <section className="baseball-field-panel" aria-label="현재 야구 경기 상황">
    <div className="field-caption"><b>그라운드 상황</b><span>{known ? occupied ? `주자 ${occupied}명` : "주자 없음" : "주자 정보 확인 중"}</span></div>
    <div className="baseball-field">
      <svg className="field-drawing" viewBox="0 0 320 290" aria-hidden="true">
        <path d="M160 265 L18 123 Q160 -35 302 123 Z" fill="#235b43" />
        <path d="M160 243 L66 149 L160 55 L254 149 Z" fill="#99734e" stroke="#f4e4c8" strokeWidth="2" />
        <path d="M160 216 L93 149 L160 82 L227 149 Z" fill="#327252" />
        <circle cx="160" cy="157" r="19" fill="#99734e" />
        <path d="M153 241 H167 V250 L160 256 L153 250 Z" fill="#fff4d9" />
      </svg>
      {positions.map(([key, label]) => {
        const base = bases[key];
        const state = base?.occupied === true ? "occupied" : base?.occupied === false ? "empty" : "unknown";
        const name = state === "occupied" ? base.runner || "주자 있음 · 이름 미제공" : state === "empty" ? "주자 없음" : "확인 중";
        return <div key={key} className={`field-base field-${key} is-${state}`} aria-label={`${label}: ${name}`}>
          <i aria-hidden="true" />
          <b>{label}{state === "occupied" ? " · 출루" : ""}</b><span>{name}</span>
        </div>;
      })}
      <div className="field-pitcher"><small>투수</small><b>{live?.pitcher || "확인 중"}</b></div>
      <div className="field-batter"><small>타자</small><b>{live?.batter || "확인 중"}</b></div>
    </div>
    <div className="field-counts" aria-label="볼 스트라이크 아웃">
      {[["B", "balls", 3], ["S", "strikes", 2], ["O", "outs", 2]].map(([label, key, max]) => <span key={key} aria-label={`${label} ${live?.[key] ?? "확인 중"}`}>
        <b>{label}</b>{Number.isInteger(live?.[key]) ? Array.from({length:max}, (_, i) => <i key={i} className={i < live[key] ? `is-on count-${key}` : ""} aria-hidden="true" />) : <small>—</small>}
      </span>)}
    </div>
    {live?.next_batter && <p className="field-next">다음 타자 <b>{live.next_batter}</b></p>}
  </section>;
}
