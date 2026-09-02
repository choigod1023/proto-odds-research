// 데이터 자동 갱신 — 페이지를 열어 둔 채로도 최신이 되게.
//
// ⚠️ 예전에는 **처음 한 번만** fetch 했다(useEffect 의존성 []). 수집기는 30분마다
//    새 JSON 을 올리는데 화면은 첫 로드 시점에 멈춰 있어서, 새로고침을 눌러야만
//    바뀌었다. "웹페이지에 동적으로 갱신이 안 된다"는 지적이 정확히 이것이었다.
//
// 세 가지를 한다:
//   1. 일정 주기로 다시 받는다
//   2. 탭을 다시 볼 때 즉시 받는다 — 사람이 실제로 알아채는 순간이 여기다
//   3. 네트워크가 돌아오면 받는다
// 그리고 **내용이 같으면 상태를 안 바꾼다** — 리렌더가 헛돌면 열어 둔
// <details> 가 닫히는 등 조작 중이던 화면이 튄다.

import { useEffect, useRef, useState } from "react";

/** GitHub Pages 가 JSON 을 캐시하므로 매번 쿼리를 붙여 우회한다. */
const bust = (url) => `${url}${url.includes("?") ? "&" : "?"}_=${Date.now()}`;

/**
 * @param {Record<string,string>} sources  {키: URL}
 * @param {number} everyMs                 갱신 주기
 * @returns {{data: object, at: number|null}}  data[키] = JSON (실패 시 null)
 */
export function usePolledData(sources, everyMs = 300000) {
  const [state, setState] = useState({ data: {}, at: null });
  // 직전 원문을 들고 있다가 같으면 넘어간다(문자열 비교가 가장 싸고 확실하다)
  const raw = useRef({});
  const keys = Object.keys(sources).join("|");
  const urls = Object.values(sources).join("|");

  useEffect(() => {
    let stop = false;
    const entries = keys.split("|").map((k, i) => [k, urls.split("|")[i]]);

    async function load() {
      const next = {};
      let changed = false;
      await Promise.all(entries.map(async ([k, url]) => {
        try {
          const r = await fetch(bust(url), {
            cache: "no-store",
            headers: { "Cache-Control": "no-cache" },
          });
          if (!r.ok) throw new Error(String(r.status));
          const text = await r.text();
          if (raw.current[k] !== text) { raw.current[k] = text; changed = true; }
          next[k] = JSON.parse(text);
        } catch {
          // 한 파일이 실패해도 나머지는 살린다. 이전 값이 있으면 그대로 유지.
          next[k] = raw.current[k] ? JSON.parse(raw.current[k]) : null;
        }
      }));
      if (!stop && (changed || state.at === null)) {
        setState({ data: next, at: Date.now() });
      }
    }

    load();
    const timer = setInterval(load, everyMs);
    const onVisible = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", load);
    return () => {
      stop = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", load);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keys, urls, everyMs]);

  return state;
}
